# herdr link-hints

EasyMotion-style keyboard link hints for [herdr](https://herdr.dev).

Press one key and every link on the focused pane gets a letter. Press the letter and the
link opens. Press it uppercase and the link is copied instead.

## Install

From GitHub:

```
herdr plugin install <owner>/<repo>
```

Or from a local checkout:

```
herdr plugin link /path/to/link-hints
```

Then bind a key in `~/.config/herdr/config.toml`:

```toml
[[keys.command]]
key = "prefix+u"
type = "plugin_action"
command = "linkhints.toggle"
description = "link hints"
```

Apply it with `herdr server reload-config`.

## Usage

| Key | Action |
| --- | --- |
| `prefix+u` | label every link on the focused pane |
| a label (`a`, `s`, `d`, …) | open that link |
| a label uppercased | copy that link to the clipboard |
| `esc`, `q`, `ctrl+c`, or `prefix+u` again | dismiss |

Recognised: `http(s)`, `ftp`, `file`, `git`, `ssh`, `mailto`, and bare `www.` hosts (opened
as `https`).

## Requirements

- `python3` (3.8+), no third-party packages
- Linux: `xdg-open`, plus `wl-clipboard` or `xclip` for copying
- macOS: `open` and `pbcopy`, both built in

## License

MIT — see [LICENSE](LICENSE).
