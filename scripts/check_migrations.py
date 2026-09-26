"""Dependency/privilege lint for the five SQL drafts; does not connect to a DB.

This deliberately is not a PostgreSQL syntax or PL/pgSQL execution validator.
Quoted strings/comments/dollar bodies are tokenized so commas and semicolons
inside SQL expressions do not masquerade as DDL boundaries.
"""
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
TOKEN = re.compile(r"--[^\n]*|/\*.*?\*/|\$([\w]*)\$.*?\$\1\$|'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*|\d+|[^\s]", re.S)

def tokens(source):
    return [m[0] for m in TOKEN.finditer(source) if not m[0].startswith(('--', '/*'))]

def split(items, delimiter=','):
    result, current, depth = [], [], 0
    for token in items:
        if token == delimiter and depth == 0:
            result.append(current)
            current = []
            continue
        current.append(token)
        depth += (token == '(') - (token == ')')
        if depth < 0: raise ValueError('Unbalanced parentheses')
    if depth: raise ValueError('Unbalanced parentheses')
    if current: result.append(current)
    return result

def parens(t, start):
    assert t[start] == '(', t[start:]
    depth = 1
    for end in range(start + 1, len(t)):
        depth += (t[end] == '(') - (t[end] == ')')
        if not depth: return t[start+1:end], end+1
    raise ValueError('Unclosed parentheses')

def check():
    paths = sorted((ROOT/'supabase/migrations').glob('*.sql'))
    errors, warnings = [], []
    tables, required, keys, indexes, fks, policies = {}, {}, {}, {}, [], set()
    functions, definers, trigger_functions, owners, revokes = set(), set(), [], {}, set()
    enabled, forced = set(), set()
    grants = []
    sources = []
    def error(message): errors.append(message)
    def key(name, cols): keys.setdefault(name, set()).add(tuple(cols))
    def constraint(name, t):
        u = [v.upper() for v in t]
        if 'REFERENCES' in u:
            pos = u.index('REFERENCES')
            schema, target = t[pos+1], t[pos+3]
            ref, _ = parens(t,pos+4)
            if 'FOREIGN' in u:
                local, _ = parens(t,u.index('KEY')+1)
                local = [v for v in local if v != ',']
            else: local = [t[0]]
            ref = [v for v in ref if v != ',']
            fks.append((name,tuple(local),target,tuple(ref)))
            if schema == 'public':
                if target not in tables: error(f'{name}: references unavailable table {target}')
                if tuple(ref) not in keys.get(target,set()): error(f'{name}: missing referenced candidate key {target}{tuple(ref)}')
            elif (schema,target,ref) != ('auth','users',['id']): error(f'Unknown external FK {schema}.{target}')
        if 'UNIQUE' in u or 'PRIMARY' in u:
            pos = u.index('UNIQUE') if 'UNIQUE' in u else u.index('KEY')
            if pos+1 < len(t) and t[pos+1] == '(':
                cols,_ = parens(t,pos+1)
                key(name,[v for v in cols if v != ','])
            else: key(name,[t[0]])
    for path in paths:
        source = path.read_text(encoding='utf-8')
        sources.append(source)
        statements = split(tokens(source),';')
        if statements[0] != ['BEGIN'] or statements[-1] != ['COMMIT']: error(f'{path.name}: missing transaction boundary')
        for t in statements:
            u = [v.upper() for v in t]
            if u[:2] == ['CREATE','TABLE']:
                name = t[4]
                if name in tables: error(f'duplicate table {name}')
                body,_ = parens(t,5)
                parts = split(body)
                columns = {p[0] for p in parts if p[0].upper() not in ['CONSTRAINT','FOREIGN','UNIQUE','CHECK','PRIMARY']}
                tables[name] = columns
                required[name] = {p[0] for p in parts if p[0] in columns and 'NOT' in p and 'NULL' in p and 'DEFAULT' not in p}
                # Local unique constraints exist before this table's FK checks.
                for p in parts:
                    if 'REFERENCES' not in [v.upper() for v in p]: constraint(name,p)
                for p in parts:
                    if 'REFERENCES' in [v.upper() for v in p]: constraint(name,p)
            elif u[:2] == ['ALTER','TABLE']:
                name = t[4]
                if name not in tables: error(f'ALTER unknown table {name}')
                if 'ENABLE' in u: enabled.add(name)
                if 'FORCE' in u: forced.add(name)
                if 'ADD' in u:
                    for part in split(t[5:]):
                        if part[0].upper() == 'ADD':
                            # Later migrations add columns; grants may reference them.
                            if part[1].upper() == 'COLUMN' and name in tables: tables[name].add(part[2])
                            constraint(name,part[1:])
            elif u[:2] == ['CREATE','INDEX'] or u[:3] == ['CREATE','UNIQUE','INDEX']:
                pos = u.index('ON')
                name = t[pos+3]
                cols,end = parens(t,pos+4)
                cols = tuple(v for v in cols if v != ',')
                signature = (name,cols,tuple(t[end:]))
                if signature in indexes: warnings.append(f'duplicate index {t[u.index("INDEX")+1]} / {indexes[signature]}')
                indexes[signature] = t[u.index('INDEX')+1]
                if u[1] == 'UNIQUE' and 'WHERE' not in u: key(name,cols)
            elif u[:2] == ['CREATE','POLICY']:
                name=t[2]
                if name in policies: error(f'duplicate policy {name}')
                policies.add(name)
                relation = t[u.index('ON')+3]
                if relation not in tables: error(f'policy unknown table {relation}')
            elif u[:2] == ['CREATE','FUNCTION'] or u[:4] == ['CREATE','OR','REPLACE','FUNCTION']:
                name=t[4] if u[1] == 'FUNCTION' else t[6]
                # OR REPLACE legitimately redefines a function from an earlier migration.
                if u[1] == 'FUNCTION' and name in functions: error(f'duplicate function {name}')
                functions.add(name)
                if 'DEFINER' in u: definers.add(name)
                if 'SECURITY' not in u or "''" not in t: error(f'function missing security/search path {name}')
            elif u[:2] == ['ALTER','FUNCTION'] and 'OWNER' in u:
                owners[t[4]] = t[-1]
            if u[0] == 'REVOKE' and 'FUNCTION' in u:
                for pos,v in enumerate(u):
                    if v == 'PUBLIC' and pos+2 < len(t) and t[pos+1] == '.': revokes.add(t[pos+2])
            if u[:2] == ['CREATE','TRIGGER'] or u[:3] == ['CREATE','CONSTRAINT','TRIGGER']:
                pos=u.index('EXECUTE')
                trigger_functions.append(t[pos+4])
            if u[0] == 'GRANT' and 'ON' in u and u[u.index('ON')+1] not in ['FUNCTION','SCHEMA']:
                pos = u.index('ON')
                if t[pos+1] == 'public':
                    end = u.index('TO')
                    names = [part[2] for part in split(t[pos+1:end])]
                    for name in names:
                        if name not in tables: error(f'grant unknown table {name}')
                        for privilege in split(t[1:pos]):
                            if '(' in privilege:
                                cols,_ = parens(privilege,1)
                                for col in cols:
                                    if col != ',' and col not in tables.get(name,set()): error(f'grant unknown column {name}.{col}')
                        grants.append((name,t[end+1:],t[1:pos]))
    for name,local,target,ref in fks:
        for col in local:
            if col not in tables[name]: error(f'FK unknown local column {name}.{col}')
        if target in tables:
            for col in ref:
                if col not in tables[target]: error(f'FK unknown target column {target}.{col}')
        if len(local)!=len(ref): error(f'FK arity mismatch {name}->{target}')
    for name in tables:
        if name not in enabled or name not in forced: error(f'missing ENABLE/FORCE RLS {name}')
    for fn in trigger_functions:
        if fn not in functions: error(f'trigger missing function {fn}')
    for fn in functions:
        if fn not in revokes: error(f'function PUBLIC execute not revoked {fn}')
    for fn in definers:
        if owners.get(fn) not in ['app_foundation_reader','app_integrity_guard']:
            error(f'definer function lacks isolated owner {fn}')
    for (name,cols,predicate),index_name in indexes.items():
        if not predicate and cols in keys.get(name,set()) and not index_name.endswith('_key'):
            warnings.append(f'index duplicates candidate key: {index_name}')
    for name,roles,privileges in grants:
        if any(r in roles for r in ['PUBLIC','anon','authenticated','service_role']): error(f'browser grant {name}')
        if name in ['mailbox_connections','oauth_flows'] and 'app_worker_general' in roles: error(f'general worker secret access {name}')
        if 'DELETE' in privileges and name not in ['lead_list_memberships','sequence_steps','campaign_mailboxes','campaign_step_attachments','personalization_research_cache','personalization_previews']: error(f'history DELETE grant {name}')
        if privileges[0] == 'INSERT' and '(' in privileges:
            cols,_ = parens(privileges,1)
            missing=required[name]-set(cols)
            # Supplemental column grants can satisfy the same role's INSERT.
            for other_name,other_roles,other_privileges in grants:
                if name==other_name and roles==other_roles and other_privileges[0]=='INSERT':
                    missing -= set(other_privileges)
            if missing: error(f'INSERT cannot supply required {name} columns {sorted(missing)} for {roles}')
    if len(paths) < 6 or len(tables) != 63: error(f'expected at least 6 files / 63 tables, found {len(paths)} / {len(tables)}')
    for warning in warnings: print('WARNING:',warning)
    for message in errors: print('ERROR:',message)
    print(f'{len(paths)} migrations; {len(tables)} tables; {len(fks)} FKs; {len(policies)} policies; {len(functions)} functions; {len(grants)} table grants')
    print(f'{len(errors)} errors; {len(warnings)} index warnings. Static structural checks only; SQL not executed.')
    return 1 if errors else 0

if __name__ == '__main__': sys.exit(check())
