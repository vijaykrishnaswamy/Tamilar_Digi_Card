import ast
import io

p = "app/state.py"
s = io.open(p, encoding="utf-8").read()

s = s.replace(
    "def upsert_member(email: str, membership_number: str, status: str) -> Dict[str, Any]:",
    'def upsert_member(email: str, membership_number: str, status: str,\n'
    '                  member_names: Optional[List[str]] = None,\n'
    '                  expiry_date: str = "") -> Dict[str, Any]:',
)

s = s.replace(
    '        status_changed = existing.get("status") != status',
    '        status_changed = (\n'
    '            existing.get("status") != status\n'
    '            or (member_names and existing.get("member_names") != member_names)\n'
    '            or (expiry_date and existing.get("expiry_date") != expiry_date)\n'
    '        )',
)

s = s.replace(
    '        payload = {\n'
    '            "membership_number": membership_number,\n'
    '            "status": status,\n'
    '            "updated_at": _now(),\n'
    '        }',
    '        payload = {\n'
    '            "membership_number": membership_number,\n'
    '            "status": status,\n'
    '            "member_names": member_names or existing.get("member_names") or [],\n'
    '            "expiry_date": expiry_date or existing.get("expiry_date") or "",\n'
    '            "updated_at": _now(),\n'
    '        }',
)

s = s.replace(
    '        "membership_number": membership_number,\n'
    '        "status": status,\n'
    '        "link_token"',
    '        "membership_number": membership_number,\n'
    '        "status": status,\n'
    '        "member_names": member_names or [],\n'
    '        "expiry_date": expiry_date or "",\n'
    '        "link_token"',
)

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
ast.parse(s)
print("state.py OK")
print("  member_names refs:", s.count("member_names"))
print("  expiry_date refs :", s.count("expiry_date"))
