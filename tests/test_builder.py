import unittest

from playlist_builder import (
    BuildError,
    alphabetical_key,
    parse_source,
    rewrite_extinf_group,
)


class PlaylistBuilderTests(unittest.TestCase):
    def test_rewrites_existing_group_and_preserves_other_metadata(self):
        line = '#EXTINF:-1 tvg-id="abc" group-title="Old" tvg-logo="logo.png",Alpha TV'
        rewritten, name = rewrite_extinf_group(line, "New Group")
        self.assertEqual(name, "Alpha TV")
        self.assertIn('tvg-id="abc"', rewritten)
        self.assertIn('tvg-logo="logo.png"', rewritten)
        self.assertIn('group-title="New Group"', rewritten)
        self.assertNotIn('group-title="Old"', rewritten)

    def test_adds_group_and_preserves_attached_directive(self):
        source = b"\n".join(
            [
                b"#EXTM3U",
                b'#EXTINF:-1 tvg-id="abc",Zulu',
                b"#EXTVLCOPT:http-user-agent=Example",
                b"https://example.com/zulu.m3u8",
            ]
        )
        result = parse_source(source, "https://example.com/list.m3u", 0, "Test")
        self.assertEqual(len(result.entries), 1)
        self.assertEqual(result.entries[0].name, "Zulu")
        self.assertEqual(result.entries[0].lines[1], "#EXTVLCOPT:http-user-agent=Example")
        self.assertIn('group-title="Test"', result.entries[0].lines[0])

    def test_rewrites_extgrp_when_present(self):
        source = b"\n".join(
            [
                b"#EXTM3U",
                b"#EXTINF:-1,Alpha",
                b"#EXTGRP:Old Group",
                b"https://example.com/alpha.m3u8",
            ]
        )
        result = parse_source(source, "https://example.com/list.m3u", 0, "New Group")
        self.assertEqual(result.entries[0].lines[1], "#EXTGRP:New Group")

    def test_natural_alphabetical_sort(self):
        names = ["Channel 48", "Éclair", "channel 6", "Alpha"]
        self.assertEqual(
            sorted(names, key=alphabetical_key),
            ["Alpha", "channel 6", "Channel 48", "Éclair"],
        )

    def test_rejects_missing_stream_url(self):
        source = b"#EXTM3U\n#EXTINF:-1,Broken\n"
        with self.assertRaises(BuildError):
            parse_source(source, "https://example.com/broken.m3u", 0, "Test")


if __name__ == "__main__":
    unittest.main()
