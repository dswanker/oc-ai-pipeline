import base64, subprocess, sys
src, rest = sys.argv[1], sys.argv[2:]
b64 = base64.b64encode(open(src, 'rb').read()).decode()
def remote(cmd, timeout=600):
    assert "'" not in cmd
    r = subprocess.run(['railway', 'ssh', '--', f"sh -c '{cmd}'"], capture_output=True, text=True, timeout=timeout)
    return r.returncode, (r.stdout or '').replace('Using SSH key', ''), (r.stderr or '')
remote('rm -f /tmp/rr.b64 /tmp/rr.py')
for i in range(0, len(b64), 2500):
    rc, out, err = remote(f"printf %s {b64[i:i + 2500]} >> /tmp/rr.b64")
    if rc: sys.exit("chunk failed: " + err[:120])
remote("base64 -d /tmp/rr.b64 > /tmp/rr.py")
rc, out, err = remote("cd /app && python3 /tmp/rr.py " + " ".join(rest))
print(out); print(err[-600:] if rc else '')
remote('rm -f /tmp/rr.b64 /tmp/rr.py')
