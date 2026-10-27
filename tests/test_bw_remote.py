import importlib.machinery
import importlib.util
import os
import sys
import tempfile
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


class ViaTest(unittest.TestCase):
    def setUp(self):
        self.machine = login("Macchina", [field("remote_type", "ssh"), field("remote_name", "mach"),
                                          field("remote_host", "10.0.0.1"), field("remote_key", "~/.ssh/k")],
                             username="admin")
        self.site = note("SFTP site", [field("remote_type", "sftp"), field("remote_name", "site.it"),
                                       field("remote_host", "10.0.0.1"), field("remote_user", "site"),
                                       field("remote_via", "mach"), field("remote_path", "/srv/siti/site")])

    def resolve(self, items, target):
        with mock.patch.object(bw, "warn"):
            return {r.name: r for r in bw.resolve_names(bw.collect(items), target)}

    def test_ssh_config_main_name_goes_via_machine(self):
        ssh = self.resolve([self.machine, self.site], "ssh")
        self.assertEqual(sorted(ssh), ["mach", "site.it", "site.it.sftp"])
        self.assertEqual((ssh["site.it"].user, ssh["site.it"].key), ("admin", "~/.ssh/k"))
        self.assertEqual(ssh["site.it.sftp"].user, "site")
        out = bw.render_ssh(list(ssh.values()), set())
        self.assertIn("Host site.it\n  HostName 10.0.0.1\n  User admin\n  Port 22\n  IdentityFile ~/.ssh/k\n", out)

    def test_rclone_main_name_is_direct_and_via_is_alias(self):
        rclone = self.resolve([self.machine, self.site], "rclone")
        self.assertEqual(sorted(rclone), ["mach", "site.it", "site.it.ssh"])
        with mock.patch.object(bw, "obscure", side_effect=lambda pwd: "OBS"):
            out = bw.build_rclone(list(rclone.values()), "")
        self.assertRegex(out, r"\[site.it\]\ntype = sftp\ndescription = .*\nhost = 10.0.0.1\nuser = site\n")
        self.assertRegex(out, r"\[site.it.ssh\]\ntype = alias\ndescription = .*\nremote = mach:/srv/siti/site\n")

    def test_via_sudo_uses_ssh_client_and_site_user(self):
        self.site["fields"].append(field("remote_via_sudo", "site"))
        rclone = self.resolve([self.machine, self.site], "rclone")
        with mock.patch.object(bw, "obscure", side_effect=lambda pwd: "OBS"):
            out = bw.build_rclone(list(rclone.values()), "")
        self.assertIn("remote = mach,ssh='ssh -o BatchMode=yes -o LogLevel=ERROR mach',"
                      "server_command='sudo -n -u site /usr/lib/openssh/sftp-server',"
                      "md5sum_command='sudo -n -u site md5sum',sha1sum_command='sudo -n -u site sha1sum',"
                      "shell_type='unix':/srv/siti/site\n", out)

    def test_via_sudo_needs_via(self):
        with self.assertRaises(bw.ItemError):
            bw.parse_remote(note(fields=[field("remote_type", "ssh"), field("remote_name", "n"),
                                         field("remote_host", "h"), field("remote_user", "u"),
                                         field("remote_via_sudo", "x")]))

    def test_via_only_item(self):
        site = note("Solo via", [field("remote_type", "ssh"), field("remote_name", "site.it"),
                                 field("remote_via", "mach"), field("remote_path", "/srv/x")])
        self.assertEqual(sorted(self.resolve([self.machine, site], "ssh")), ["mach", "site.it"])
        self.assertEqual(sorted(self.resolve([self.machine, site], "rclone")), ["mach", "site.it"])

    def test_unknown_machine_and_path_without_via(self):
        with mock.patch.object(bw, "warn") as w:
            remotes = bw.collect([self.site])
        self.assertEqual([r.rtype for r in remotes], ["sftp"])
        w.assert_called_once()
        with self.assertRaises(bw.ItemError):
            bw.parse_remote(note(fields=[field("remote_type", "ssh"), field("remote_name", "n"),
                                         field("remote_host", "h"), field("remote_user", "u"),
                                         field("remote_path", "/x")]))


def sftp(name="web01", rev="r1", **kw):
    data = dict(item_id="id1", item_name="Server 1", revision=rev, kind="sftp", tls=False,
                name=name, host="h", user="u", port=22, pwd="pw")
    data.update(kw)
    return bw.Remote(**data)


DESC = "description = bw-remote id1 r1 Server 1"


class RcloneTest(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(bw, "obscure", side_effect=lambda pwd: "OBS-" + pwd)
        p.start()
        self.addCleanup(p.stop)

    def test_managed_sections_appended_and_manual_kept(self):
        text = "# mio\n[manual]\ntype = local\n"
        out = bw.build_rclone([sftp(aliases=[("site.prod", "/www")])], text)
        self.assertTrue(out.startswith("# mio\n[manual]\ntype = local\n\n[web01]\n"))
        self.assertIn(f"[web01]\ntype = sftp\n{DESC}\nhost = h\nuser = u\nport = 22\npass = OBS-pw\n", out)
        self.assertIn(f"[site.prod]\ntype = alias\n{DESC}\nremote = web01:/www\n", out)

    def test_managed_sections_regenerated_manual_order_kept(self):
        text = f"[a]\ntype = local\n\n[old]\ntype = sftp\n{DESC}\n\n# commento di z\n[z]\ntype = local\n"
        out = bw.build_rclone([sftp()], text)
        self.assertNotIn("[old]", out)
        self.assertTrue(out.startswith("[a]\ntype = local\n\n# commento di z\n[z]\ntype = local\n\n[web01]\n"))

    def test_survives_rclone_rewrite_and_legacy_format(self):
        # formato 0.1 dopo che rclone ha riscritto il file: niente # END, chiavi rilevate aggiunte
        text = ("# BEGIN bw-remote\n# generato da bw-remote: non modificare a mano, viene sovrascritto\n"
                "# bw: id1 r1 Server 1\n[web01]\ntype = sftp\nhost = h\npass = OLDOBS\nshell_type = unix\n\n"
                "[manual]\ntype = local\n")
        out = bw.build_rclone([sftp()], text)
        self.assertEqual(out, "[manual]\ntype = local\n\n[web01]\ntype = sftp\n" + DESC +
                         "\nhost = h\nuser = u\nport = 22\npass = OLDOBS\nshell_type = unix\n\n")
        self.assertEqual(bw.build_rclone([sftp()], out), out)

    def test_obscured_password_reused_when_item_unchanged(self):
        first = bw.build_rclone([sftp()], "")
        bw.obscure.side_effect = lambda pwd: "NEW-" + pwd
        self.assertEqual(bw.build_rclone([sftp()], first), first)
        self.assertIn("pass = NEW-pw", bw.build_rclone([sftp(rev="r2")], first))

    def test_auto_keys_dropped_when_host_changes(self):
        first = bw.build_rclone([sftp()], "").replace("pass = OBS-pw\n", "pass = OBS-pw\nshell_type = unix\n")
        self.assertIn("shell_type = unix", bw.build_rclone([sftp()], first))
        self.assertNotIn("shell_type", bw.build_rclone([sftp(rev="r2", host="h2")], first))

    def test_manual_names_win(self):
        with mock.patch.object(bw, "warn"):
            out = bw.build_rclone([sftp(aliases=[("site.prod", "/www")])],
                                  "[web01]\ntype = local\n\n[site.prod]\ntype = local\n")
        self.assertEqual(out, "[web01]\ntype = local\n\n[site.prod]\ntype = local\n")

    def test_default_key_only_without_password_and_key(self):
        out = bw.build_rclone([sftp(name="a", pwd=""), sftp(name="b"), sftp(name="c", pwd="", key="~/.ssh/k"),
                               sftp(name="d", kind="ftp", pwd="")], "", "~/.ssh/id_rsa")
        sections = dict(bw.split_sections(out)[1])
        self.assertIn("key_file = ~/.ssh/id_rsa", sections["a"])
        self.assertFalse(any(l.startswith("key_file") for l in sections["b"] + sections["d"]))
        self.assertIn("key_file = ~/.ssh/k", sections["c"])

    def test_ftps(self):
        out = bw.build_rclone([sftp(kind="ftp", tls=True, port=21, pwd="")], "")
        self.assertIn(f"type = ftp\n{DESC}\nhost = h\nuser = u\nport = 21\nexplicit_tls = true\n", out)


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


class ConfigTest(unittest.TestCase):
    def write(self, text):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as fh:
            fh.write(text)
        self.addCleanup(os.remove, path)
        return path

    def test_load_config(self):
        path = self.write("[defaults]\nkey = ~/.ssh/altra\nfoo = bar\n\n[altro]\nx = 1\n")
        with mock.patch.object(bw, "warn") as w:
            self.assertEqual(bw.load_config(path), {"key": "~/.ssh/altra"})
        self.assertEqual(w.call_count, 2)

    def test_missing_file(self):
        self.assertEqual(bw.load_config("/nonexistent/bw-remote"), {})
        with self.assertRaises(SystemExit):
            with mock.patch("sys.stderr"):
                bw.load_config("/nonexistent/bw-remote", explicit=True)

    def test_xdg_path(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/x"}):
            self.assertEqual(bw.default_config_path(), "/x/bw-remote/config")

    def test_precedence(self):
        conf = {"key": "~/.ssh/conf", "rclone_config": "/conf/rclone.conf"}
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("RCLONE_CONFIG", None)
            args = bw.build_parser(conf).parse_args(["sync"])
            self.assertEqual((args.key, args.rclone_config, args.ssh_config),
                             ("~/.ssh/conf", "/conf/rclone.conf", "~/.ssh/config"))
            self.assertEqual(bw.build_parser(conf).parse_args(["sync", "--key", "~/k"]).key, "~/k")
            os.environ["RCLONE_CONFIG"] = "/env/rclone.conf"
            self.assertEqual(bw.build_parser(conf).parse_args(["sync"]).rclone_config, "/env/rclone.conf")
        self.assertEqual(bw.build_parser({}).parse_args(["copy-key"]).key, "~/.ssh/id_rsa")


if __name__ == "__main__":
    unittest.main()
