"""Confere arquivos públicos e alguns padrões sensíveis sem imprimir valores."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ALLOWED = {'.gitignore', 'README.md', 'SECURITY.md', 'NOTICE.md', 'AGENTS.md',
           'src/pc_tools.py', 'src/audit_delegation.py',
           'tests/test_pc_tools.py', 'tests/test_audit_delegation.py', 'tests/test_publication.py',
           'scripts/check_publication.py', 'workflows/poseclone-qwen21.json'}
PATTERNS = {
    'provider_token': re.compile(r'(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}|sk-[A-Za-z0-9_-]{30,})'),
    'private_key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    'personal_path': re.compile(r'(?i)[A-Z]:[\\/]+(?:Users[\\/]|Inteligencia Artificial[\\/])'),
    'embedded_image': re.compile(r'data:image/[a-zA-Z.+-]+;base64,'),
    'url_credentials': re.compile(r'https?://[^\s/@]+:[^\s/@]+@'),
    'literal_credential': re.compile(r'(?i)["\'](?:api_key|password|access_token|authorization)["\']\s*:\s*["\'][^"\']+["\']'),
}


def inspect(path, content):
    findings = []
    if path not in ALLOWED:
        findings.append('FILE_NOT_ALLOWED')
    if len(content) > 512 * 1024:
        findings.append('FILE_TOO_LARGE')
    try:
        text = content.decode('utf-8')
    except UnicodeDecodeError:
        return findings + ['NOT_UTF8_TEXT']
    for label, pattern in PATTERNS.items():
        if pattern.search(text):
            findings.append(label)
    return findings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staged', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    base = ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
            '-c', 'safe.directory=' + str(root), '-C', str(root)]
    if args.staged:
        response = subprocess.run(base + ['ls-files', '--cached', '-z'], capture_output=True, check=True)
        paths = [x.decode('utf-8') for x in response.stdout.split(b'\0') if x]
        data = {path: subprocess.run(base + ['show', ':' + path], capture_output=True, check=True).stdout for path in paths}
    else:
        data = {path: (root / path).read_bytes() for path in sorted(ALLOWED) if (root / path).is_file()}
    findings = [{'file': path, 'reasons': reasons} for path, content in data.items() if (reasons := inspect(path, content))]
    if not data:
        findings.append({'file': None, 'reasons': ['NO_FILES_REVIEWED']})
    missing = sorted(ALLOWED - set(data))
    if missing:
        findings.append({'file': None, 'reasons': ['MISSING_EXPECTED_FILES'], 'paths': missing})
    print(json.dumps({'status': 'FAIL' if findings else 'PASS', 'files': len(data),
                      'bytes': sum(len(x) for x in data.values()), 'findings': findings}, ensure_ascii=False))
    raise SystemExit(1 if findings else 0)


if __name__ == '__main__':
    main()
