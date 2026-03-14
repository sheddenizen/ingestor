#!/usr/bin/env python3
from flask import Flask, request, render_template_string, redirect, url_for, Response
import os
import shutil
import subprocess
import logging

logging.basicConfig(level=logging.INFO)
app = Flask(__name__)

# hard-code your source and targdir here
SOURCE_ROOT = '/home/dan/Music/asunder2'   # change to your ripped FLAC folder
TARGET_ROOT = '/home/dan/Music/test'        # change to your main FLAC library

INDEX_HTML = """
<!doctype html>
<html lang="en">
<head>
<title>FLAC Ingestor</title>
<style>
body {
    font-family: Arial, sans-serif;
    margin: 20px;
    background-color: #121212;
    color: #ffffff;
}
h1 {
    color: #bb86fc;
}
button {
    background-color: #bb86fc;
    color: #000000;
    border: none;
    padding: 10px 20px;
    cursor: pointer;
    margin-right: 10px;
}
button:hover {
    background-color: #9c4dcc;
}
pre {
    background-color: #1e1e1e;
    padding: 10px;
    border-radius: 5px;
    white-space: pre-wrap;
    word-wrap: break-word;
}
#output {
    margin-top: 20px;
}
</style>
<script>
function startStream(url) {
    console.log('Starting stream for', url);
    const output = document.getElementById('output');
    output.innerHTML = '<pre>Loading...</pre>';
    const eventSource = new EventSource(url);
    let content = '';
    eventSource.onmessage = function(event) {
        console.log('Received:', event.data);
        if (event.data === 'END') {
            eventSource.close();
        } else {
            content += event.data + '<br>';
            output.innerHTML = '<pre>' + content + '</pre>';
        }
    };
    eventSource.onerror = function() {
        console.log('EventSource error');
        output.innerHTML = '<pre>Error occurred</pre>';
        eventSource.close();
    };
}
</script>
</head>
<body>
<h1>Simple FLAC Ingestor</h1>
<p>Source folder: {{ srcdir }}</p>
<p>Target library: {{ targdir }}</p>
<button type=button onclick="startStream('/stream_check')">Check</button>
<button type=button onclick="startStream('/stream_replaygain')">Generate ReplayGain</button>
<button type=button onclick="startStream('/stream_ingest')">Ingest</button>
<div id="output"></div>
</body>
</html>
"""

def find_flac_files(srcdir):
    for root, _, files in os.walk(srcdir):
        for f in files:
            if f.lower().endswith('.flac'):
                yield os.path.join(root, f)

def metaflac_get_tags(path):
    result = {}
    try:
        p = subprocess.run(['metaflac', '--show-all-tags', path],
                           capture_output=True, text=True, check=False)
        if p.returncode != 0:
            result['error']=f"{p.stderr.strip().replace(os.linesep, ' ')}"
            return result
        out = p.stdout.strip()

        for line in out.splitlines():
            if '=' in line:
                tag, value = line.split('=', 1)
                result[tag] = value
    except FileNotFoundError:
        logging.error(f"FileNotFoundError: metaflac failed to open {path}")

    return result


def check_one(srcdir, targdir):
    tracks = 0
    last = (None, None)  # (artist, album) of last file for context
    for f in find_flac_files(srcdir):
        rel = os.path.relpath(f, srcdir)
        parts = rel.split(os.sep)
        # require exactly srcdir/artist/album/file.flac -> 3 parts
        if len(parts) != 3:
            yield f"{rel}: not under artist/album (depth={len(parts)-1})"
            continue
        artist, album, filename = parts
        if (artist, album) != last:
            yield f"Checking album: {artist}/{album}"
            last = (artist, album)
            if artist.lower().startswith('unknown') or album.lower().startswith('unknown'):
                yield f"{artist}/{album}/*: artist/album starts with 'unknown'"
        # tags: artist, album, track, replaygain
        tags = metaflac_get_tags(f)
        if 'error' in tags:
            yield f"{rel}: failed to read tags ({tags['error']})"
            continue
        artist_tag = tags.get('ARTIST')
        album_tag = tags.get('ALBUM')
        track_tag = tags.get('TRACKNUMBER') or tags.get('TITLE')
        replay_tag = tags.get('REPLAYGAIN_TRACK_GAIN') and tags.get('REPLAYGAIN_ALBUM_GAIN')
        if not artist_tag:
            yield f"{rel}: missing ARTIST tag"
        if not album_tag:
            yield f"{rel}: missing ALBUM tag"
        if not track_tag:
            yield f"{rel}: missing TRACK (TRACKNUMBER or TITLE) tag"
        if not replay_tag:
            yield f"{rel}: missing REPLAYGAIN tag"
        # album exists in targdir?
        targdir_album_dir = os.path.join(targdir, artist, album)
        if os.path.exists(targdir_album_dir):
            yield f"{rel}: album already exists in targdir library ({targdir_album_dir})"

def add_replaygain_to_all(srcdir):
    for f in find_flac_files(srcdir):
        try:
            p = subprocess.run(['metaflac', '--add-replay-gain', f],
                               capture_output=True, text=True, check=False)
            if p.returncode == 0:
                yield f"{os.path.relpath(f, srcdir)}: replaygain added"
            else:
                yield f"{os.path.relpath(f, srcdir)}: failed ({p.stderr.strip() or p.stdout.strip()})"
        except FileNotFoundError:
            yield "metaflac not installed or not found in PATH"
            return
        except Exception as e:
            logging.error(f"Error adding replaygain to {f}: {e}")
            yield f"{os.path.relpath(f, srcdir)}: error ({e})"

def do_ingest(srcdir, targdir):
    # iterate albums (artist/album directories)
    seen_albums = set()
    for artist in os.listdir(srcdir):
        artist_dir = os.path.join(srcdir, artist)
        if not os.path.isdir(artist_dir):
            continue
        for album in os.listdir(artist_dir):
            album_dir = os.path.join(artist_dir, album)
            if not os.path.isdir(album_dir):
                continue
            rel = os.path.join(artist, album)
            dest_artist_dir = os.path.join(targdir, artist)
            dest_album_dir = os.path.join(dest_artist_dir, album)
            if os.path.exists(dest_album_dir):
                yield f"{rel}: skipped (album already exists in targdir)"
                continue
            os.makedirs(dest_artist_dir, exist_ok=True)
            try:
                shutil.copytree(album_dir, dest_album_dir)
                yield f"{rel}: copied"
                logging.info(f"Successfully copied {album_dir} to {dest_album_dir}")
            except Exception as e:
                logging.error(f"Error copying {album_dir} to {dest_album_dir}: {e}")
                yield f"{rel}: failed to copy ({e})"

@app.route('/', methods=['GET'])
def index():
    srcdir = SOURCE_ROOT
    targdir = TARGET_ROOT
    return render_template_string(INDEX_HTML, srcdir=srcdir, targdir=targdir, result=None)

@app.route('/check', methods=['POST'])
def check():
    # use hard-coded paths
    srcdir = SOURCE_ROOT
    targdir = TARGET_ROOT
    if not os.path.isdir(srcdir) or not os.path.isdir(targdir):
        return render_template_string(INDEX_HTML, srcdir=srcdir, targdir=targdir,
                                      result="Source or targdir path not found.")
    problems = list(check_one(srcdir, targdir))
    if not problems:
        res = "OK: no problems found"
    else:
        res = "Problems:\n" + "\n".join(problems)
    return render_template_string(INDEX_HTML, srcdir=srcdir, targdir=targdir, result=res)

@app.route('/replaygain', methods=['POST'])
def replaygain():
    srcdir = SOURCE_ROOT
    if not os.path.isdir(srcdir):
        return render_template_string(INDEX_HTML, srcdir=srcdir, targdir=TARGET_ROOT, result="Source path not found.")
    results = list(add_replaygain_to_all(srcdir))
    return render_template_string(INDEX_HTML, srcdir=srcdir, targdir=TARGET_ROOT, result="\n".join(results))

@app.route('/ingest', methods=['POST'])
def ingest():
    srcdir = SOURCE_ROOT
    targdir = TARGET_ROOT
    if not os.path.isdir(srcdir) or not os.path.isdir(targdir):
        return render_template_string(INDEX_HTML, srcdir=srcdir, targdir=targdir,
                                      result="Source or targdir path not found.")
    report = list(do_ingest(srcdir, targdir))
    return render_template_string(INDEX_HTML, srcdir=srcdir, targdir=targdir, result="\n".join(report))

@app.route('/stream_check')
def stream_check():
    srcdir = SOURCE_ROOT
    targdir = TARGET_ROOT
    if not os.path.isdir(srcdir) or not os.path.isdir(targdir):
        def generate():
            yield f"data: Source or targdir path not found.\n\n"
            yield "data: END\n\n"
        return Response(generate(), mimetype='text/event-stream', headers={'Access-Control-Allow-Origin': '*'})
    def generate():
        problems = list(check_one(srcdir, targdir))
        if not problems:
            yield "data: OK: no problems found\n\n"
        else:
            yield "data: Problems:\n\n"
            for p in problems:
                yield f"data: {p}\n\n"
        yield "data: END\n\n"
    return Response(generate(), mimetype='text/event-stream', headers={'Access-Control-Allow-Origin': '*'})

@app.route('/stream_replaygain')
def stream_replaygain():
    srcdir = SOURCE_ROOT
    if not os.path.isdir(srcdir):
        def generate():
            yield "data: Source path not found.\n\n"
            yield "data: END\n\n"
        return Response(generate(), mimetype='text/event-stream', headers={'Access-Control-Allow-Origin': '*'})
    def generate():
        for msg in add_replaygain_to_all(srcdir):
            yield f"data: {msg}\n\n"
        yield "data: END\n\n"
    return Response(generate(), mimetype='text/event-stream', headers={'Access-Control-Allow-Origin': '*'})

@app.route('/stream_ingest')
def stream_ingest():
    srcdir = SOURCE_ROOT
    targdir = TARGET_ROOT
    if not os.path.isdir(srcdir) or not os.path.isdir(targdir):
        def generate():
            yield "data: Source or targdir path not found.\n\n"
            yield "data: END\n\n"
        return Response(generate(), mimetype='text/event-stream', headers={'Access-Control-Allow-Origin': '*'})
    def generate():
        for msg in do_ingest(srcdir, targdir):
            yield f"data: {msg}\n\n"
        yield "data: END\n\n"
    return Response(generate(), mimetype='text/event-stream', headers={'Access-Control-Allow-Origin': '*'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8005, debug=True)

