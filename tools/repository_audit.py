"""Read-only heuristic publication audit. Reports locations, never matched values."""
import argparse
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = {
    'Discord token': re.compile(r'(?<![\w-])(?:mfa\.[\w-]{60,}|[\w-]{23,28}\.[\w-]{6}\.[\w-]{27,})(?![\w-])'),
    'Discord webhook credential': re.compile(r'https://(?:\w+\.)?discord(?:app)?\.com/api/webhooks/\d+/[\w-]+'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'credential URL': re.compile(r'\b[a-z][a-z0-9+.-]*://[^\s/:\"\']+:[^\s/@\"\']+@',re.I),
    'provider token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{20,})\b'),
}
ASSIGNMENT = re.compile(r'''(?im)^[ \t]*["']?([\w.-]*(?:token|password|passwd|secret|api_key|apikey|credential)[\w.-]*)["']?[ \t]*[:=][ \t]*["']?([^\s"'#,}\r\n]+)''')
PLACEHOLDERS = {'', 'none','null','false','true','0','your_token_here','changeme','example','placeholder','test','fake','dummy'}
PRIVATE_PARTS = {'runtime','backups','logs','transcripts','tickets','exports','uploads','storage','node_modules','.venv','venv','env','__pycache__','.pytest_cache','.mypy_cache','.ruff_cache','.idea','.vscode','htmlcov','dist','build'}

def git(*args):
    return subprocess.check_output(['git',*args],cwd=ROOT)

def secret_findings(text):
    found=set()
    for label,pattern in PATTERNS.items():
        if pattern.search(text): found.add(label)
    for match in ASSIGNMENT.finditer(text):
        value=match.group(2)
        if len(value)<8 or value.casefold() in PLACEHOLDERS: continue
        if value.startswith(('os.','values.','self.','str(','(','<','$','env.','discord.')):continue
        if any(word in value.casefold() for word in ('placeholder','example','fake','dummy','your_')):continue
        # Only assignments of literal secrets, not Python expressions/docs prose.
        line=text[match.start():text.find('\n',match.start()) if '\n' in text[match.start():] else len(text)]
        rhs=re.split(r'[:=]',line,maxsplit=1)[-1].lstrip()
        if not rhs.startswith((chr(34),chr(39))) and not match.group(1).isupper():continue
        if '(' in value or '[' in value:continue
        if 'getenv(' in line or '.get(' in line or 'compile(' in line:continue
        found.add('potential credential assignment')
    return sorted(found)

def private_path(name):
    path=Path(name);low=path.name.lower()
    return (bool(set(path.parts)&PRIVATE_PARTS) or
            (low.startswith('.env') and low!='.env.example') or
            bool(re.search(r'\.(?:db|sqlite3?)(?:-(?:wal|shm|journal))?$',low)) or
            low.endswith(('.log','.pem','.key','.pyc','.bak','.zip')) or
            low.startswith(('credentials.','secrets.')))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history',action='store_true')
    parser.add_argument('--local',action='store_true',help='Also scan ignored local text, excluding runtimes/vendor/binary data.')
    args=parser.parse_args()
    names=git('ls-files','--cached','--others','--exclude-standard','-z').decode().split('\0')
    findings=[]
    for name in sorted(set(filter(None,names))):
        if private_path(name):findings.append(('publish candidate',name,'private/runtime artifact'))
        path=ROOT/name
        if not path.is_file() or path.stat().st_size>2_000_000:continue
        raw=path.read_bytes()
        if b'\0' in raw:continue
        for label in secret_findings(raw.decode('utf-8',errors='replace')):findings.append(('publish candidate',name,label))
    # Inspect staged blobs separately: the index can differ from the working tree.
    for name in filter(None,git('diff','--cached','--name-only','--diff-filter=ACMR','-z').decode().split('\0')):
        if private_path(name):findings.append(('staged',name,'private/runtime artifact'))
        raw=git('show',':'+name)
        if b'\0' not in raw:
            for label in secret_findings(raw.decode('utf-8',errors='replace')):findings.append(('staged',name,label))
    if args.local:
        import os
        candidates=set(names)
        for directory,dirs,files in os.walk(ROOT):
            dirs[:]=[d for d in dirs if d not in PRIVATE_PARTS|{'.git'}]
            for filename in files:
                path=Path(directory)/filename;name=path.relative_to(ROOT).as_posix()
                if name in candidates or path.stat().st_size>2_000_000:continue
                raw=path.read_bytes()
                if b'\0' in raw:continue
                for label in secret_findings(raw.decode('utf-8',errors='replace')):findings.append(('local only',name,label))
    if args.local:
        config=ROOT/'.git/config'
        if config.is_file():
            for label in secret_findings(config.read_text(errors='replace')):findings.append(('local only','.git/config',label))
    if args.history:
        seen=set()
        for commit in git('rev-list','--all').decode().splitlines():
            for entry in git('ls-tree','-r','-z',commit).split(b'\0'):
                if not entry:continue
                meta,name=entry.split(b'\t',1);kind,oid=meta.split()[1:]
                name=name.decode(errors='replace')
                if kind!=b'blob' or (oid,name) in seen:continue
                seen.add((oid,name))
                if private_path(name):findings.append(('history',name,'private/runtime artifact'))
                raw=git('cat-file','blob',oid.decode())
                if b'\0' in raw:continue
                for label in secret_findings(raw.decode('utf-8',errors='replace')):findings.append(('history',name,label))
        print(f'History: {len(seen)} unique file versions inspected.')
    for scope,name,label in sorted(set(findings)):print(f'{scope}: {name}: {label}')
    print(f'Publication candidates: {len(set(filter(None,names)))}; findings: {len(set(findings))}. Heuristic scan; manual review still required.')
    raise SystemExit(any(scope!='local only' for scope,_,_ in findings))

if __name__=='__main__':main()
