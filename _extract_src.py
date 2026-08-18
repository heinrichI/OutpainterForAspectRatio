import ctypes
import sys

"""Extract --src, --ratio, compute numeric ratio, and clean args from argv.
Usage: python _extract_src.py <tempfile> [env_ratio] [original args...]
Writes 4 lines to <tempfile>:
  line 1: <src>            (value after --src, or first positional, or empty)
  line 2: <ratio_override> (value after --ratio, or empty)
  line 3: <ratio_num>      (numeric aspect ratio, 2 decimals, for subfolder;
                            computed from --ratio, else env_ratio, else screen)
  line 4: <cleaned args>   (original args minus --src/--out and their values,
                            minus the drag-and-drop positional path)
"""


def screen_resolution():
    try:
        user32 = ctypes.windll.user32
        w, h = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        if w > 0 and h > 0:
            return w, h
    except Exception:
        pass
    return 1920, 1080


def numeric_ratio(raw):
    """Return the numeric aspect ratio rounded to 2 decimals as a string."""
    s = str(raw or '').strip().lower().replace(' ', '')
    if s in ('screen', 'monitor', 'display', ''):
        w, h = screen_resolution()
    else:
        s = s.replace(':', 'x').replace('/', 'x')
        if 'x' in s:
            a, _, b = s.partition('x')
            try:
                w, h = float(a), float(b)
            except ValueError:
                return '1.78'
        else:
            try:
                r = float(s)
                return f"{r:.2f}"
            except ValueError:
                return '1.78'
    if h <= 0:
        return '1.78'
    return f"{w / h:.2f}"


if len(sys.argv) < 2:
    sys.exit(1)
temp_path = sys.argv[1]
argv = sys.argv[2:]
env_ratio = 'screen'
if argv and not argv[0].startswith('--'):
    env_ratio = argv[0]
    argv = argv[1:]
args = argv

src = ''
ratio_override = ''
cleaned = []
i = 0
while i < len(args):
    a = args[i]
    if a == '--src' and i + 1 < len(args):
        src = args[i + 1]
        i += 2
        continue
    if a == '--out' and i + 1 < len(args):
        i += 2
        continue
    if a == '--ratio' and i + 1 < len(args):
        ratio_override = args[i + 1]
        cleaned.append(a)
        cleaned.append(args[i + 1])
        i += 2
        continue
    if a.startswith('-'):
        cleaned.append(a)
        i += 1
        continue
    # positional: first one is the drag-and-drop source path
    if not src:
        src = a
    else:
        cleaned.append(a)
    i += 1

ratio_num = numeric_ratio(ratio_override or env_ratio or 'screen')
with open(temp_path, 'w', encoding='utf-8') as f:
    f.write(src + '\n')
    f.write(ratio_override + '\n')
    f.write(ratio_num + '\n')
    f.write(' '.join(cleaned) + '\n')