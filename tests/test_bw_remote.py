import importlib.machinery
import importlib.util
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
_loader = importlib.machinery.SourceFileLoader("bw_remote", os.path.join(HERE, "..", "bw-remote"))
_spec = importlib.util.spec_from_loader("bw_remote", _loader)
bw = importlib.util.module_from_spec(_spec)
sys.modules["bw_remote"] = bw
_loader.exec_module(bw)


def field(name, value, type_=0, linked=None):
    return {"name": name, "value": value, "type": type_, "linkedId": linked}


def login(name="Server", fields=(), username="u", password="p", uris=(), rev="2026-10-01T00:00:00.000Z"):
    return {"id": f"id-{name}", "name": name, "type": 1, "revisionDate": rev, "fields": list(fields),
            "login": {"username": username, "password": password, "uris": [{"uri": u} for u in uris]}}


def note(name="Nota", fields=(), rev="2026-10-01T00:00:00.000Z"):
    return {"id": f"id-{name}", "name": name, "type": 2, "revisionDate": rev, "fields": list(fields)}


class ParseTest(unittest.TestCase):
    def test_item_without_remote_type_is_ignored(self):
        self.assertIsNone(bw.parse_remote(login(fields=[field("remote_name", "x")])))

    def test_login_falls_back_to_native_fields(self):
        r = bw.parse_remote(login(fields=[field("remote_type", "ssh"), field("remote_name", "web01")],
                                  uris=["https://panel.example.com", "sftp://web01.example.com:2222"]))
        self.assertEqual((r.kind, r.user, r.pwd, r.host, r.port), ("sftp", "u", "p", "web01.example.com", 2222))

    def test_custom_fields_win(self):
        r = bw.parse_remote(login(fields=[field("remote_type", "sftp"), field("remote_name", "web01"),
                                          field("remote_host", "h"), field("remote_user", "other"),
                                          field("remote_pwd", "secret", 1)]))
        self.assertEqual((r.user, r.pwd, r.port), ("other", "secret", 22))

    def test_linked_fields(self):
        r = bw.parse_remote(login(fields=[field("remote_type", "ftp"), field("remote_name", "f"),
                                          field("remote_host", "h"), field("remote_user", None, 3, 100)],
                                  username="linkeduser"))
        self.assertEqual((r.kind, r.user, r.port), ("ftp", "linkeduser", 21))

    def test_note_needs_custom_fields(self):
        with self.assertRaises(bw.ItemError):
            bw.parse_remote(note(fields=[field("remote_type", "ssh"), field("remote_name", "n")]))

    def test_ftps_aliases_and_opts(self):
        r = bw.parse_remote(note(fields=[
            field("remote_type", "FTPS"), field("remote_name", "f"), field("remote_host", "h"),
            field("remote_user", "u"), field("remote_alias", "site.prod:/www"),
            field("remote_alias", "site.old:"), field("remote_opt", "rclone:no_check_certificate=true")]))
        self.assertTrue(r.tls)
        self.assertEqual(r.aliases, [("site.prod", "/www"), ("site.old", "")])
        self.assertEqual(r.rclone_opts, {"no_check_certificate": "true"})

    def test_invalid_values(self):
        base = [field("remote_name", "n"), field("remote_host", "h"), field("remote_user", "u")]
        for extra in ([field("remote_type", "smb")],
                      [field("remote_type", "ssh"), field("remote_port", "abc")],
                      [field("remote_type", "ssh"), field("remote_opt", "foo")]):
            with self.assertRaises(bw.ItemError):
                bw.parse_remote(note(fields=base + extra))

    def test_old_flag_from_item_name(self):
        f = [field("remote_type", "ssh"), field("remote_name", "n"), field("remote_host", "h")]
        self.assertTrue(bw.parse_remote(login("OLD FTP sito", f)).old)
        self.assertTrue(bw.parse_remote(login("Server sito OLD", f)).old)
        self.assertFalse(bw.parse_remote(login("Server Golden", f)).old)

    def test_alias_clashes_are_dropped(self):
        a = [field("remote_type", "ssh"), field("remote_host", "h"), field("remote_alias", "x.prod:/a")]
        with mock.patch.object(bw, "warn") as w:
            remotes = bw.collect([login("A", a + [field("remote_name", "a")]),
                                  login("B", a + [field("remote_name", "b")])])
        self.assertEqual([r.aliases for r in remotes], [[("x.prod", "/a")], []])
        w.assert_called_once()


def item_for(name, rtype, item_name, rev="r1"):
    return login(item_name, [field("remote_type", rtype), field("remote_name", name),
                             field("remote_host", "h-" + item_name)], rev=rev)


class ResolveNamesTest(unittest.TestCase):
    def resolve(self, items, target):
        with mock.patch.object(bw, "warn"):
            return {r.name: r.item_name for r in bw.resolve_names(bw.collect(items), target)}

    def test_old_item_gets_suffix(self):
        items = [item_for("site.it", "ssh", "OLD FTP site"), item_for("site.it", "sftp", "SFTP site")]
        expected = {"site.it": "SFTP site", "site.it.old": "OLD FTP site"}
        self.assertEqual(self.resolve(items, "rclone"), expected)
        self.assertEqual(self.resolve(items, "ssh"), expected)

    def test_ssh_and_sftp_items_split_by_file(self):
        items = [item_for("site.it", "ssh", "SSH root"), item_for("site.it", "sftp", "SFTP chroot")]
        self.assertEqual(self.resolve(items, "rclone"), {"site.it": "SFTP chroot", "site.it.ssh": "SSH root"})
        self.assertEqual(self.resolve(items, "ssh"), {"site.it": "SSH root", "site.it.sftp": "SFTP chroot"})

    def test_single_item_keeps_name_and_ftp_stays_out_of_ssh(self):
        items = [item_for("a.it", "ssh", "A"), item_for("b.it", "ftp", "B")]
        self.assertEqual(self.resolve(items, "rclone"), {"a.it": "A", "b.it": "B"})
        self.assertEqual(self.resolve(items, "ssh"), {"a.it": "A"})

    def test_same_type_newest_wins_and_suffix_is_unique(self):
        items = [item_for("s.it", "sftp", "vecchio", rev="r1"), item_for("s.it", "sftp", "nuovo", rev="r2"),
                 item_for("s.it", "sftp", "altro OLD"), item_for("s.it", "sftp", "altro2 OLD")]
        self.assertEqual(self.resolve(items, "rclone"),
                         {"s.it": "nuovo", "s.it.sftp": "vecchio", "s.it.old": "altro OLD", "s.it.old-2": "altro2 OLD"})


def sftp(name="web01", rev="r1", **kw):
    data = dict(item_id="id1", item_name="Server 1", revision=rev, kind="sftp", tls=False,
                name=name, host="h", user="u", port=22, pwd="pw")
    data.update(kw)
    return bw.Remote(**data)


class RcloneTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(bw, "obscure", side_effect=lambda pwd: "OBS-" + pwd)
        p.start()
        self.addCleanup(p.stop)

    def test_block_appended_and_outside_kept(self):
        text = "[manual]\ntype = local\n"
        out = bw.build_rclone([sftp(aliases=[("site.prod", "/www")])], text)
        self.assertTrue(out.startswith("[manual]\ntype = local\n\n# BEGIN bw-remote\n"))
        self.assertIn("[web01]\ntype = sftp\nhost = h\nuser = u\nport = 22\npass = OBS-pw\n", out)
        self.assertIn("[site.prod]\ntype = alias\nremote = web01:/www\n", out)
        self.assertTrue(out.endswith("# END bw-remote\n"))

    def test_block_replaced_in_place(self):
        text = "[a]\ntype = local\n\n# BEGIN bw-remote\n[old]\n# END bw-remote\n\n[z]\ntype = local\n"
        out = bw.build_rclone([sftp()], text)
        self.assertNotIn("[old]", out)
        self.assertTrue(out.startswith("[a]\ntype = local\n\n# BEGIN bw-remote\n"))
        self.assertTrue(out.endswith("# END bw-remote\n\n[z]\ntype = local\n"))

    def test_obscured_password_reused_when_item_unchanged(self):
        first = bw.build_rclone([sftp()], "")
        bw.obscure.side_effect = lambda pwd: "NEW-" + pwd
        self.assertEqual(bw.build_rclone([sftp()], first), first)
        self.assertIn("pass = NEW-pw", bw.build_rclone([sftp(rev="r2")], first))

    def test_conflicts_outside_block_are_skipped(self):
        with mock.patch.object(bw, "warn"):
            out = bw.build_rclone([sftp(aliases=[("site.prod", "/www")])], "[web01]\ntype = local\n")
        block = bw.BLOCK_RE.search(out).group(0)
        self.assertNotIn("[web01]", block)
        self.assertNotIn("[site.prod]", block)

    def test_ftps(self):
        out = bw.build_rclone([sftp(kind="ftp", tls=True, port=21, pwd="")], "")
        self.assertIn("type = ftp\nhost = h\nuser = u\nport = 21\nexplicit_tls = true\n", out)


class SshTest(unittest.TestCase):
    def test_render(self):
        r = sftp(key="~/.ssh/id_x", ssh_aliases=["site.it"], ssh_opts={"ProxyJump": "bastion"})
        out = bw.render_ssh([r, sftp(name="ftp1", kind="ftp")], set())
        self.assertIn("Host web01 site.it\n  HostName h\n  User u\n  Port 22\n  IdentityFile ~/.ssh/id_x\n"
                      "  ProxyJump bastion\n", out)
        self.assertNotIn("ftp1", out)

    def test_existing_hosts_are_skipped(self):
        with mock.patch.object(bw, "warn"):
            out = bw.render_ssh([sftp(ssh_aliases=["site.it"])], bw.ssh_hosts("Host a site.it\n  User x\n"))
        self.assertIn("Host web01\n", out)

    def test_include_added_once_on_top(self):
        path = os.path.expanduser("~/.ssh/config.d/bw-remote")
        out = bw.ensure_include("Host a\n  User x\n", path)
        self.assertTrue(out.startswith("Include ~/.ssh/config.d/bw-remote\n\nHost a\n"))
        self.assertEqual(bw.ensure_include(out, path), out)


if __name__ == "__main__":
    unittest.main()
