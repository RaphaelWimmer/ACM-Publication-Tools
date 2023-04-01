#!/usr/bin/env python3

# Public Domain / CC-0
# (0) 2022 Raphael Wimmer <raphael.wimmer@ur.de>

INFO = """This script downloads a spreadsheet of camera-ready submissions from PCS.
Afterwards, it optionally downloads all final PDFs, videos and zip files with supplementary 
materials which are linked in the spreadsheet.
To do this, pass parameters `--all`, `--pdf`, `--video`, `--supplement` or a combination of these.
You need a file fields.csv that contains the metadata for each track

The downloaded spreadsheet is called `camera_ready.csv`.
Files are stored in folders ./PDF/, etc., as configured in fields.csv.
Files are named `{last part of DOI from CSV}-{file description}.{EXT}`, as configured in fields.csv
This is the format required by ACM for upload in the DL.

You can provide credentials for PCS in the environment variables PCS_USER / PCS_PASSWORD or enter them once prompted

"""

# stdlib
import re
import os
import time
import sys
from csv import DictReader
from urllib.request import urlopen, urlretrieve, HTTPError
import getpass

# additional dependencies
import click
import requests
from tqdm import tqdm


def validate_track_id(track):
    if re.match(r"^[a-z]{2,}\d{2}[a-z]+$", track):
        return track
    else:
        raise click.BadParameter("Last parameter needs to be the conference track ID from PCS (e.g. 'chi23b')")

##################################

#print(INFO)

PCS_LOGIN_URL = "https://new.precisionconference.com/user/login"
PCS_TRACK_LIST_URL = "https://new.precisionconference.com/get_table?table_id=user_chairing&conf_id=&type_id="
PCS_SPREADSHEET_URL_PREFIX = "https://new.precisionconference.com/"
PCS_SPREADSHEET_URL_SUFFIX = "/pubchair/csv/camera"
#PCS_USER = os.environ.get('PCS_USER') or input("PCS user: ")
#PCS_PASSWORD = os.environ.get('PCS_PASSWORD') or getpass.getpass("PCS password: ")
#LIST_FILE = f"./{PCS_CONF_ID}_submissions.csv"
LIST_FILE_SUFFIX = "_camera_ready.csv"
FIELDS_FILE_SUFFIX = "_fields.csv"


def file_is_current(file_path, max_seconds=300):
    file_mtime = os.path.getmtime(file_path)
    current_time = time.time()
    return (current_time - file_mtime) < max_seconds


def get_available_tracks(user, password, print_them=False):
    print("Getting list of tracks ... ")
    pcs_session = requests.Session()
    r = pcs_session.get(PCS_LOGIN_URL)
    csrf_token = re.search(r'name="csrf_token" type="hidden" value="([a-z0-9#]+)"', r.text).groups()[0]
    r = pcs_session.post(PCS_LOGIN_URL, data={'username': user, 'password': password, 'csrf_token': csrf_token})
    r = pcs_session.get(PCS_TRACK_LIST_URL)
    roles = r.json()['data']
    available_tracks = {}
    for role in roles:
        title = role[0]
        match = re.match(r'<a href="/(\w+)/(\w+)">(.+)</a>', role[3])
        track_id = match.group(1)
        role_id = match.group(2)
        track_name = match.group(3)
        if print_them:
            print(f"{title} ({role_id}): {track_name} ({track_id})")
        if role_id in ['pubchair', 'chair']:
            available_tracks[track_id] = role_id     # "chi23b": "pubchair" (or "chair")
    return available_tracks


# you want to re-download the csv file every time because the download links for all media files
# are regenerated by PCS on download. If you use a 'stale' csv file, you will get 401 errors when 
# trying to download PDFs and other files.

def get_camera_ready_csv(track_id, user, password, overwrite=True):
    # get current data from PCS
    list_file = f"{track_id}{LIST_FILE_SUFFIX}"
    if overwrite is False and os.path.exists(list_file):
        print("file already exists - skipping download")
        return
    if os.path.exists(list_file) and file_is_current(list_file, 5 * 60):
        print("file already downloaded less than five minutes ago - skipping download")
        return
    print("Downloading camera_ready.csv ... ")
    pcs_session = requests.Session()
    r = pcs_session.get(PCS_LOGIN_URL)
    csrf_token = re.search(r'name="csrf_token" type="hidden" value="([a-z0-9#]+)"', r.text).groups()[0]
    r = pcs_session.post(PCS_LOGIN_URL, data={'username': user, 'password': password, 'csrf_token': csrf_token})
    r = pcs_session.get(PCS_SPREADSHEET_URL_PREFIX + track_id + PCS_SPREADSHEET_URL_SUFFIX)
    with open(list_file, "wb") as fd:
        fd.write(r.content)
    print("done.")


def get_filetypes(typefile):
    try:
        fd = open(typefile, "r")
        dr = DictReader(fd)
        filetypes = []
        for dic in dr:
            filetypes.append(dic)
        return filetypes
    except:
        print(f"No file with field definitions found (looking for {typefile}")
        sys.exit(1)


def download_file(paper_id, url, filename, overwrite="modified"):
    try:
        doc = None
        # avoid unnecessary downloads
        if overwrite == "none":
            if os.path.exists(filename):  # only download if file changed
                tqdm.write("   >... already downloaded")
                return True
        elif overwrite == "modified":
            doc = urlopen(url, timeout=10)
            doc_size = int(doc.getheader("Content-Length"))
            #print(f" ({doc_size/1000000.0:.2f} MB)")
            if os.path.exists(filename):  # only download if file changed
                file_size = os.stat(filename).st_size
                if file_size == doc_size:
                    tqdm.write("   >... already downloaded")
                    return True
        # ok, we want to download the file. make request if not already done
        if not doc:
            doc = urlopen(url, timeout=10)
            doc_size = int(doc.getheader("Content-Length"))
        with open(filename, 'wb') as fd:
            #print(f" ({doc_size/1000000.0:.2f} MB)")
            progress_bar = tqdm(total=doc_size, unit='iB', unit_scale=True, leave=False)
            while True:
                data = doc.read(1024*100)
                if not data:
                    break
                fd.write(data)
                progress_bar.update(len(data))
            progress_bar.close()
            return True
    except (ValueError, HTTPError) as e:
        tqdm.write("   >... file not found on server")
        print(e)
        return False


# overwrite: 
# "all" download files regardless of whether they already exist
# "modified" get HTTP header for each file and only downloade existing files if local file size is different than server file size.
# "none" only download files that do not already exist locally (this misses files that have been modified recently but is faster than checking file sizes

def download_files(track_id, filetypes, start_index=0, overwrite="modified"):
    for filetype in filetypes:
        try:
            os.makedirs(f"{track_id}_{filetype['directory']}")
        except FileExistsError:
            print(f"directory '{track_id}_{filetype['directory']}' already exists, writing into it")

    fd = open(f"{track_id}{LIST_FILE_SUFFIX}", encoding='utf-8-sig')  # CSV has BOM
    submissions = list(DictReader(fd))  # load in memory so that we get the line count
    for idx, submission in enumerate(tqdm(submissions, desc="Submissions processed", leave=False)):
        tqdm.write(f"[{idx}] Paper: {submission['Paper ID']} ({submission['Title']})")
        if idx < start_index:
            tqdm.write("    skipping")
            continue
        for filetype in filetypes:
            try:
                if len(submission[filetype['pcs_field']]) > 1:
                    tqdm.write(f"    Retrieving '{filetype['description']}'")
                    paper_id = submission['Paper ID']
                    filename = f"{track_id}_{filetype['directory']}/{paper_id}{filetype['suffix']}"
                    url = submission[filetype['pcs_field']]
                    if download_file(paper_id, url, filename, overwrite):
                        pass 
                        #print("done")
                    else:
                        tqdm.write("failed")
                        return idx
                else:
                    tqdm.write(f"   >... '{filetype['description']}' not submitted")
            except KeyError:
                tqdm.write(f"   >... field {filetype['pcs_field']} not in CSV")
    fd.close()


def print_status(track_id, filetypes, verbose=False):
    if len(filetypes) == 0:
        sys.exit()
    missing = {}
    for filetype in filetypes:
        missing[filetype['description']] = []
    fd = open(f"{track_id}{LIST_FILE_SUFFIX}", encoding='utf-8-sig')  # CSV has BOM
    submissions = DictReader(fd)
    for idx, submission in enumerate(submissions):
        if verbose:
            print(f"[{idx}] Paper: {submission['Paper ID']} ({submission['Title']})")
        for filetype in filetypes:
            try:
                doi = submission['DOI'].split("/")[-1]  # https://doi.org/10.1145/3491102.3501897 -> 3491102.3501897
                paper_id = submission['Paper ID']
                if len(submission[filetype['pcs_field']]) < 1:
                    if verbose:
                        print(f"   >... '{filetype['description']}' not submitted")
                    missing[filetype['description']].append(paper_id)
                else:
                    if verbose:
                        print(f"   >... '{filetype['description']}' submitted")
            except KeyError:
                print(f"   >... field {filetype['pcs_field']} not in CSV")
    fd.close()
    for filetype in filetypes:
        print(f"'{filetype['description']}' ({track_id}) still missing:")
        if len(missing[filetype['description']]) > 0:
            print(", ".join(missing[filetype['description']]))
        else:
            print("none!")
        print("")
        print("")

    """
    tracks,dl_flag,pcs_field,description,directory,suffix,mimetype,upload_to_dl,ready_field
pn,video,Video Figure (Optional),Video Figure,VID,-video-figure.mp4,video/mp4,yes,
pn,video,Video Figure Captions (Required if the video figure contains spoken dialog),Video Figure Captions,VID_SRT,-video-figure-captions.vtt,text/vtt,yes,
pn,preview,video_preview,Video Preview,PRV,-video-preview.mp4,video/mp4,yes,
pn,preview,video_preview_captions,Video Preview Captions,PRV_SRT,-video-preview-caption.vtt,text/vtt,yes,
pn,talk,Pre-recorded Video of Talks,Talk Video,TLK,-talk-video.mp4,video/mp4,acmdl_agreement,
pn,talk,Video Presentation Caption,Talk Video Captions,TLK_SRT,-talk-video-caption.vtt,text/vtt,acmdl_agreement,
pn,supplement,Supplemental Materials (Optional),Supplemental Materials,SUP,-supplemental-materials.zip,application/zip,yes,
    """
def create_fields_file(track_id, fields_file):
    # if exists, exit
    print(f"Downloading spreadsheet for: {track_id}")
    FIELDS = "tracks,dl_flag,pcs_field,description,directory,suffix,mimetype,upload_to_dl,ready_field".split(',')
    pcs_fields = None
    ft = {'pdf': {'folder': 'PDF', 'ext': '.pdf', 'mime': 'application/pdf', 'upload': 'no', 'ready_field': ''},
          'video': {'folder': 'VID', 'ext': '-video.mp4', 'mime': 'video/mp4', 'upload': 'yes', 'ready_field': ''},
          'subtitles': {'folder': 'VID', 'ext': '-subtitles.vtt', 'mime': 'text/vtt', 'upload': 'yes', 'ready_field': ''},
          'supplement': {'folder': 'SUP', 'ext': '-supplemental-materials.zip', 'mime': 'application/zip', 'upload': 'yes', 'ready_field': ''},
          'source': {'folder': 'SRC', 'ext': '-source.zip', 'mime': 'application/zip', 'upload': 'no', 'ready_field': ''},
          'zip': {'folder': 'ZIP', 'ext': '.zip', 'mime': 'application/zip', 'upload': 'no', 'ready_field': ''},
          }
    fd = open(f"{track_id}{LIST_FILE_SUFFIX}", encoding='utf-8-sig')  # CSV has BOM
    submissions = DictReader(fd)
    for submission in submissions:
        if not pcs_fields:
            pcs_fields = {key: None for key in submission.keys()}
        for field in pcs_fields.keys():
            if submission[field].startswith("http"):   # we have an URL
                if ".mp4" in submission[field]:
                    pcs_fields[field] = "video"
                if ".srt" in submission[field]:
                    pcs_fields[field] = "subtitles"
                if ".pdf" in submission[field]:
                    pcs_fields[field] = "pdf"
                if ".zip" in submission[field]:
                    if "upplement" in field:
                        pcs_fields[field] = "supplement"
                    elif "ource" in field:
                        pcs_fields[field] = "source"
                    else:
                        pcs_fields[field] = "zip"
    fd.close()
    field_file_lines = ["tracks,dl_flag,pcs_field,description,directory,suffix,mimetype,upload_to_dl,ready_field\n"]
    for field, fieldtype in pcs_fields.items():
        print(f"{field}: {fieldtype}")
        if fieldtype:
            field_file_lines.append(f'{track_id},{fieldtype},"{field}","{field}",{ft[fieldtype]["folder"]},{ft[fieldtype]["ext"]},{ft[fieldtype]["mime"]},{ft[fieldtype]["upload"]},{ft[fieldtype]["ready_field"]}\n')
    print(field_file_lines)
    with open(fields_file + ".test", "w") as fd:
        fd.writelines(field_file_lines)


@click.command()
@click.option('--user', prompt=True, help='PCS user (can also be set via environment variable PCS_USER)')
@click.option("--password", prompt=True, hide_input=True)
@click.option('--overwrite', type=click.Choice(['all', 'none', 'modified']), default='modified')
@click.option('--start', 'start_index', default=0, help='start download at n-th line of CSV (good for resuming failed downloads')
@click.option('--status', is_flag=True, show_default=True, default=False, help='only print status of submissions')
@click.option('--tracks', is_flag=True, show_default=True, default=False, help='only print available tracks')
@click.option('--guess_fields', is_flag=True, show_default=True, default=False, help='try to automatically create a configuration file with fields for this track')
@click.argument('track_id')
@click.argument('dl_flags', nargs=-1)
def download(track_id, dl_flags, overwrite, start_index, status, tracks, guess_fields, user, password):
    """Download files from PCS."""
    if tracks:
        print("Checking which tracks you have access to...")
        available_tracks = get_available_tracks(user, password, True)
        if track_id not in available_tracks.keys():
            print(f"You don't seem to have 'chair' or 'pubchair' access to track '{track_id}'.")
        sys.exit(1)

    fields_file = f"{track_id}{FIELDS_FILE_SUFFIX}"
    if guess_fields:
        print(f"Downloading spreadsheet for: {track_id}")
        get_camera_ready_csv(track_id, user, password)
        create_fields_file(track_id, fields_file)
        print("Field file generated - please check it!")
        sys.exit(0)
    all_filetypes = get_filetypes(fields_file)

    # here we loop through the _fields.csv file and collect all filetypes that we want to download
    # on the command line, we give download flags which may map to one or more actual filetypes

    if "all" in dl_flags:
        filetypes = all_filetypes
    else:
        # check for invalid dl_flags
        acceptable_dl_flags = []
        for ft in all_filetypes:
            acceptable_dl_flags.append(ft['dl_flag'])
        acceptable_dl_flags = set(acceptable_dl_flags)
        accepted_dl_flags = []
        for dl_flag in dl_flags:
            if dl_flag in acceptable_dl_flags:
                accepted_dl_flags.append(dl_flag)
            else:
                print(f"Warning: '{dl_flag}' not configured in {fields_file} - ignored!")
        if len(dl_flags) > 0 and len(accepted_dl_flags) == 0:
            print("No acceptable download flags provided")
            print(f"Acceptable download flags are: {', '.join(acceptable_dl_flags)}")
            sys.exit(1)

        filetypes = []
        for ft in all_filetypes:
            if ft['dl_flag'] in accepted_dl_flags:
                filetypes.append(ft)
        
    print(f"Downloading spreadsheet for: {track_id}")
    get_camera_ready_csv(track_id, user, password)
    if status:
        print_status(track_id, filetypes)
        return
    if len(filetypes) == 0:
        print("Done!")
        return  # finished

    print(f"Downloading files for: {track_id}")
    while True:  # reload camera-ready.csv on error
        start_index = download_files(track_id, filetypes, start_index, overwrite=overwrite)
        if start_index is None:   # finished
            break
        else:
            print(f"Restarting at submission #{start_index}")
            get_camera_ready_csv(track_id, user, password)
    print("Done!")


if __name__ == "__main__":
    download(auto_envvar_prefix='PCS')
