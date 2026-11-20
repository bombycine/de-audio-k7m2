"""Write GCP_TTS_KEY to a file, tolerating common copy/paste damage (missing outer braces).
Prints only structural diagnostics, never key content."""
import json, os, sys
raw = os.environ.get("GCP_TTS_KEY", "").strip()
if not raw:
    sys.exit("GCP_TTS_KEY secret is empty")
for label, cand in [("as-is", raw), ("added {", "{" + raw), ("added }", raw + "}"), ("added { }", "{" + raw + "}")]:
    try:
        key = json.loads(cand)
        if key.get("type") == "service_account" and "private_key" in key:
            break
    except json.JSONDecodeError:
        continue
else:
    sys.exit(f"GCP_TTS_KEY is not valid service-account JSON (length {len(raw)}, "
             f"starts with {raw[:1]!r}, ends with {raw[-1:]!r}). Re-paste the whole file, including the outer {{ }}.")
if label != "as-is":
    print(f"note: repaired pasted key ({label}); consider re-pasting the secret")
open(sys.argv[1], "w").write(json.dumps(key))
print(f"key ok: project {key.get('project_id')}")
