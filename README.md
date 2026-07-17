# TiviMate playlist builder

This repository compiles the 46 configured `iptv-org/iptv` stream files into one M3U playlist with 18 groups in a fixed order. TiviMate reads one stable playlist URL, while GitHub Actions checks every upstream source hourly.

## Output behavior

The builder:

1. Downloads all 46 exact source URLs from `sources.json`.
2. Stops without publishing if any source fails, is empty, is malformed, or has no channels.
3. Preserves every channel entry, duplicate entry, channel name, `tvg-id`, logo, stream URL, and attached playback directives.
4. Changes only the channel group metadata so every channel is assigned to its requested group.
5. Sorts channels alphabetically inside each group using a deterministic, case-insensitive, accent-insensitive natural sort.
6. Writes groups in the exact order defined in `sources.json`.
7. Embeds the requested EPG URL in the M3U header as both `url-tvg` and `x-tvg-url`.
8. Publishes `output/playlist.m3u` and an audit file at `output/manifest.json`.

No deduplication is performed. If the same channel or stream appears more than once upstream, every occurrence remains in the compiled playlist.

## Stability model

Publishing is atomic. The output is written only after all 46 sources download and parse successfully. If one source is temporarily unavailable, the workflow fails and the last known-good playlist remains published. The next hourly run tries again.

The workflow runs at minute 17 of every hour rather than at the top of the hour, because GitHub documents that scheduled jobs are more likely to be delayed during the first minutes of an hour.

A weekly heartbeat commit prevents GitHub from disabling the schedule after 60 days without repository activity. Normal commits occur only when the compiled playlist changes.

## Deploy on GitHub

1. Create a public GitHub repository. A public repository is necessary for an Android TV device to read a raw file without authentication.
2. Upload this entire folder, including `.github/workflows/build-playlist.yml`.
3. Open the repository's **Actions** tab and run **Build TiviMate playlist** once with **Run workflow**.
4. If the workflow cannot push its output, open **Settings**, then **Actions**, then **General**, and set **Workflow permissions** to **Read and write permissions**.
5. After the first successful run, use this URL in TiviMate:

```text
https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/YOUR_REPOSITORY/main/output/playlist.m3u
```

Replace `YOUR_GITHUB_USERNAME` and `YOUR_REPOSITORY` with your repository details. If your default branch is not `main`, replace `main` as well.

## TiviMate settings

Menu names can vary slightly by TiviMate version.

1. Open **Settings**, then **Playlists**, then **Add playlist**, then **M3U playlist**.
2. Enter the raw GitHub URL shown above.
3. Name the playlist, for example, `International TV`.
4. Set **Update playlist on app start** to on.
5. Set the playlist **Update interval** to `1 hour`, when that value is available in your installed version.
6. Set group sorting to **By order in playlist**.
7. Set channel sorting to **By order in playlist**. The builder has already alphabetized each group.
8. Add the EPG manually as a fallback under **Settings**, then **EPG**, then **EPG sources**:

```text
https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz
```

9. Assign that EPG source to the compiled playlist, then update the playlist and EPG once manually.

The playlist header also contains the EPG URL, but assigning it manually in TiviMate is the more explicit configuration and is easier to troubleshoot.

## EPG considerations

The selected `ALL_SOURCES1` EPG is very large. EPGShare warns that the combined file can exceed the memory capacity of some devices. The builder keeps your requested URL unchanged. If TiviMate becomes slow or runs out of storage, replace it in `sources.json` with smaller regional EPG files and rerun the workflow.

EPG coverage depends on exact `tvg-id` matching. This builder preserves every upstream `tvg-id`; it does not invent or remap guide identifiers.

## Run locally

Python 3.11 or newer is sufficient. The project uses only the Python standard library.

```bash
python -m unittest discover -s tests -v
python playlist_builder.py
```

Generated files:

```text
output/playlist.m3u
output/manifest.json
```

## Change the source list

Edit `sources.json`. Group order is the order of the `groups` array. Source order is used only as a stable tie-breaker when two channels have the same display name.

The executable includes a safety check requiring exactly 46 sources and 18 groups. Remove or revise that check in `playlist_builder.py` only when you intentionally change the requested topology.

## Scope and availability

This project compiles links; it does not host, proxy, repair, or test the video streams themselves. A channel can remain in the source playlist while its destination stream is offline, geoblocked, expired, or incompatible with a device. The upstream repository also states that it stores links rather than video files and does not control the linked destinations.
