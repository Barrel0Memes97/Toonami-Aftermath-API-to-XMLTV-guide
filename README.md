# Toonami Aftermath XMLTV Guide for Jellyfin

A small Python script that pulls TV schedules from the Toonami Aftermath API and turns them into an **XMLTV guide** that Jellyfin can use. It also serves the generated `toonami.xml` file over HTTP, so Jellyfin can refresh the guide automatically.

The script uses Python 3 and only the standard library, so there are no Python packages to install.

## Channels

| Channel | Guide data | XMLTV channel ID |
| --- | --- | --- |
| Toonami Aftermath East | Yes | `toonami-aftermath-east` |
| Toonami Aftermath West | Yes | `toonami-aftermath-west` |
| Snickelodeon East | Yes | `snickelodeon-east` |
| Snickelodeon West | Yes | `snickelodeon-west` |
| Toonami Aftermath Movies | No | `toonami-aftermath-movies` |

Movies is included as a channel even though the API doesn't provide its schedule. Its video stream is:

```text
http://api.toonamiaftermath.com:3000/movies/playlist.m3u8
```

**The stream and the guide are separate.** This script makes the guide; it does not add video streams to Jellyfin. Add the Movies URL to your M3U playlist or as an additional M3U tuner.

For an existing M3U playlist, you can append:

```m3u
#EXTINF:-1 tvg-id="toonami-aftermath-movies" tvg-name="Toonami Aftermath Movies" group-title="Toonami Aftermath",Toonami Aftermath Movies
http://api.toonamiaftermath.com:3000/movies/playlist.m3u8
```

## Installing on Linux

These instructions are for **Debian or Ubuntu with systemd**. Put these four files in the same folder before starting:

- `toonami_xmltv_with_movies.py`
- `toonami-xmltv.service`
- `toonami-xmltv.timer`
- `toonami-xmltv-web.service`

Open a terminal in that folder and run:

```bash
sudo apt update
sudo apt install -y python3
sudo useradd --system --home /opt/toonami-xmltv --shell /usr/sbin/nologin toonamiepg
sudo mkdir -p /opt/toonami-xmltv/www
sudo chown -R toonamiepg:toonamiepg /opt/toonami-xmltv
sudo install -o toonamiepg -g toonamiepg -m 0755 toonami_xmltv_with_movies.py /opt/toonami-xmltv/toonami_xmltv.py
sudo cp toonami-xmltv.service toonami-xmltv.timer toonami-xmltv-web.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now toonami-xmltv-web.service toonami-xmltv.timer
sudo systemctl start toonami-xmltv.service
```

The timer refreshes the guide every **30 minutes**, and the web service serves the XMLTV file on **port 8787**.

### Add it to Jellyfin

Go to **Dashboard → Live TV → TV Guide Data Providers** and add an XMLTV provider with this URL:

```text
http://192.168.254.53:8787/toonami.xml
```

Remove anything prefilled in the categories sections.

That address is for the Debian VM used in this setup. If you're installing it on a different machine, replace `192.168.254.53` with that machine's IP address.

Keep your existing M3U tuner for the actual video streams, then match the channels to their XMLTV guide entries in Jellyfin.

## Updating an existing installation

If you already installed an earlier version, you only need to replace the Python script. From the folder containing the new file, run:

```bash
sudo install -o toonamiepg -g toonamiepg -m 0755 toonami_xmltv_with_movies.py /opt/toonami-xmltv/toonami_xmltv.py
sudo systemctl start toonami-xmltv.service
sudo systemctl restart toonami-xmltv-web.service
```

Your Jellyfin XMLTV URL stays the same.

## How it works

The script requests upcoming shows from the Toonami Aftermath API, reads the schedule for each supported channel, and writes the results into an XMLTV file.

The API endpoint is:

```text
https://api.toonamiaftermath.com/channelsCurrentMedia?startDate=2026-10-13T21%3A00%3A00Z
```

The `startDate` parameter tells the API which point in time to request. The script makes additional requests at later times to collect more of the upcoming schedule, rather than relying on just one response. By default, it aims to collect roughly 24 hours of programming, making requests every 180 minutes. The supplied systemd service may override that interval to 30 minutes using `TA_SAMPLE_MINUTES`.

### Example API response

Here is a shortened example based on the actual response. Each channel has a `media` list containing shows and their start times:

```json
[
  {
    "name": "Toonami Aftermath East",
    "media": [
      {
        "name": "Sailor Moon",
        "startDate": "2026-10-13T21:26:19.257Z",
        "info": {
          "year": 1995,
          "episode": "Birthday Blues (Part 1)"
        },
        "blockName": "Toonami",
        "mediaType": "series",
        "episodeNumber": 94
      },
      {
        "name": "DBZ",
        "startDate": "2026-10-13T21:51:28.052Z",
        "info": {
          "fullname": "Dragon Ball Z",
          "episode": "Dende's Demise"
        },
        "blockName": "Toonami",
        "mediaType": "series",
        "episodeNumber": 70
      }
    ]
  }
]
```

The script uses each show's `startDate` as its start time and the **next show's start time** as its end time. It includes episode titles, numbers, years, and artwork when they're available. If the last show in a channel has no following start time, the script leaves its end time unspecified rather than making one up.

Requests can overlap, so the script removes duplicate entries before generating the XMLTV file. It also saves the first raw API response as `last-api-response.json` alongside the guide, which is useful if the API format ever changes.

## What about the other channels?

At the end of the API response, these channels appear with only a name:

```json
{
  "name": "MTV97"
},
{
  "name": "Movies"
},
{
  "name": "Toonami Aftermath Radio"
},
{
  "name": "Live Code"
}
```

Unfortunately, those channels **don't expose their schedules through this API response**. There is no `media` list, which means there are no program names or start times for the script to turn into a TV guide.

That's why **MTV97**, **Toonami Aftermath Radio**, and **Live Code** aren't included. **Movies is the exception**: it's a cool channel, and we have its stream URL, so I've included it even though it won't have guide listings from this API.

If those channels start exposing schedule data later, they could be added to the guide as well.

## Files and settings

The generated guide is stored at:

```text
/opt/toonami-xmltv/www/toonami.xml
```

The web service makes it available at `http://YOUR-LINUX-IP:8787/toonami.xml`.

The script supports a few environment settings if you want to change its behavior later:

| Setting | Default | Purpose |
| --- | --- | --- |
| `TA_HORIZON_HOURS` | `24` | How far ahead to request guide data |
| `TA_SAMPLE_MINUTES` | `180` | Minutes between API request points; a systemd service can override this |
| `TA_HTTP_PORT` | `8787` | Port used to serve the guide |
| `TA_OUTPUT_DIR` | `/opt/toonami-xmltv/www` | Where the guide and API response are saved |

The guide refresh schedule is controlled separately by `toonami-xmltv.timer`.
