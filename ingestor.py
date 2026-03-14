#!/usr/bin/env python3
from flask import Flask, request, render_template_string, redirect, url_for
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
<title>FLAC Ingestor</title>
<h1>Simple FLAC Ingestor</h1>
<p>Source folder: {{ srcdir }}</p>
<p>Target library: {{ targdir }}</p>
<form method=post action="{{ url_for('check') }}">
  <button type=submit>Check</button>
</form>
<form method=post action="{{ url_for('replaygain') }}">
  <button type=submit>Generate ReplayGain</button>
</form>
<form method=post action="{{ url_for('ingest') }}">
  <button type=submit>Ingest</button>
</form>
{% if result %}
<pre>{{ result }}</pre>
{% endif %}
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
    problems = []
    tracks = 0
    last = (None, None)  # (artist, album) of last file for context
    for f in find_flac_files(srcdir):
        rel = os.path.relpath(f, srcdir)
        parts = rel.split(os.sep)
        # require exactly srcdir/artist/album/file.flac -> 3 parts
        if len(parts) != 3:
            problems.append(f"{rel}: not under artist/album (depth={len(parts)-1})")
            continue
        artist, album, filename = parts
        if (artist, album) != last:
            problems.append(f"Checking album: {artist}/{album}")
            last = (artist, album)
            if artist.lower().startswith('unknown') or album.lower().startswith('unknown'):
                problems.append(f"{artist}/{album}/*: artist/album starts with 'unknown'")
        # tags: artist, album, track, replaygain
        tags = metaflac_get_tags(f)
        if 'error' in tags:
            problems.append(f"{rel}: failed to read tags ({tags['error']})")
            continue
        artist_tag = tags.get('ARTIST')
        album_tag = tags.get('ALBUM')
        track_tag = tags.get('TRACKNUMBER') or tags.get('TITLE')
        replay_tag = tags.get('REPLAYGAIN_TRACK_GAIN') and tags.get('REPLAYGAIN_ALBUM_GAIN')
        if not artist_tag:
            problems.append(f"{rel}: missing ARTIST tag")
        if not album_tag:
            problems.append(f"{rel}: missing ALBUM tag")
        if not track_tag:
            problems.append(f"{rel}: missing TRACK (TRACKNUMBER or TITLE) tag")
        if not replay_tag:
            problems.append(f"{rel}: missing REPLAYGAIN tag")
        # album exists in targdir?
        targdir_album_dir = os.path.join(targdir, artist, album)
        if os.path.exists(targdir_album_dir):
            problems.append(f"{rel}: album already exists in targdir library ({targdir_album_dir})")
    return problems

def add_replaygain_to_all(srcdir):
    results = []
    for f in find_flac_files(srcdir):
        try:
            p = subprocess.run(['metaflac', '--add-replay-gain', f],
                               capture_output=True, text=True, check=False)
            if p.returncode == 0:
                results.append(f"{os.path.relpath(f, srcdir)}: replaygain added")
            else:
                results.append(f"{os.path.relpath(f, srcdir)}: failed ({p.stderr.strip() or p.stdout.strip()})")
        except FileNotFoundError:
            return ["metaflac not installed or not found in PATH"]
        except Exception as e:
            logging.error(f"Error adding replaygain to {f}: {e}")
            results.append(f"{os.path.relpath(f, srcdir)}: error ({e})")

    return results

def do_ingest(srcdir, targdir):
    report = []
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
                report.append(f"{rel}: skipped (album already exists in targdir)")
                continue
            os.makedirs(dest_artist_dir, exist_ok=True)
            try:
                shutil.copytree(album_dir, dest_album_dir)
                report.append(f"{rel}: copied")
                logging.info(f"Successfully copied {album_dir} to {dest_album_dir}")
            except Exception as e:
                logging.error(f"Error copying {album_dir} to {dest_album_dir}: {e}")
                report.append(f"{rel}: failed to copy ({e})")
    return report

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
    problems = check_one(srcdir, targdir)
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
    results = add_replaygain_to_all(srcdir)
    return render_template_string(INDEX_HTML, srcdir=srcdir, targdir=TARGET_ROOT, result="\n".join(results))

@app.route('/ingest', methods=['POST'])
def ingest():
    srcdir = SOURCE_ROOT
    targdir = TARGET_ROOT
    if not os.path.isdir(srcdir) or not os.path.isdir(targdir):
        return render_template_string(INDEX_HTML, srcdir=srcdir, targdir=targdir,
                                      result="Source or targdir path not found.")
    report = do_ingest(srcdir, targdir)
    return render_template_string(INDEX_HTML, srcdir=srcdir, targdir=targdir, result="\n".join(report))

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=8005, debug=True)

