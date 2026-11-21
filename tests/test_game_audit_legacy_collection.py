"""Owner approval and read-only capabilities; no live Docker/Discord/SQLite."""
import io
import hashlib
import sqlite3
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from tools import game_audit_legacy_collect as owner
from tools import game_audit_legacy_runtime as runtime


def binding(tmp_path):
    return {'environment':'production','guildId':1,'applicationId':100,
            'database':str(tmp_path/'fixture.db'),'runtimeUid':10001,'runtimeGid':10001,
            'containerId':'a'*64,'imageId':'sha256:'+'b'*64,'observerCommit':'a'*40}


class TTY(io.StringIO):
    def __init__(self,reply):super().__init__();self.reply=reply
    def fileno(self):return 42
    def readline(self,*a):return self.reply


def test_approval_requires_exact_private_binding_not_a_general_phrase(tmp_path,monkeypatch):
    value=binding(tmp_path);expected=owner.PHRASE+owner.digest(value)
    monkeypatch.setattr(os,'isatty',lambda fd:True)
    assert owner.confirm(TTY(expected),value)==owner.digest(value)
    value['guildId']=2
    with pytest.raises(ValueError,match='OWNER_CANCELLED'):owner.confirm(TTY(expected),value)
    with pytest.raises(ValueError,match='OWNER_CANCELLED'):owner.confirm(TTY(owner.PHRASE),value)


def test_unattended_input_cannot_approve_or_publish_private_binding(tmp_path,monkeypatch):
    tty=TTY('anything');monkeypatch.setattr(os,'isatty',lambda fd:False)
    with pytest.raises(ValueError,match='OWNER_TTY_REQUIRED'):owner.confirm(tty,binding(tmp_path))
    assert not tty.getvalue()


def guard(tmp_path):
    result=runtime.Guard({'binding':binding(tmp_path),'verifiedFiles':{}})
    return result


@pytest.mark.parametrize('event,args',[
    ('open',('/private/file','wb',os.O_WRONLY)),('open',('/private/file','r+',os.O_RDWR)),
    ('os.mkdir',('/private/folder',0o700)),('os.remove',('/private/file',-1)),
    ('subprocess.Popen',('anything',)),('os.system',('anything',)),('socket.bind',('socket',('0.0.0.0',80))),
])
def test_writes_processes_and_listeners_are_denied(tmp_path,event,args):
    with pytest.raises(runtime.Rejected):guard(tmp_path).audit(event,args)


def test_no_database_connection_during_import_or_to_another_path(tmp_path):
    gate=guard(tmp_path)
    with pytest.raises(runtime.Rejected):gate.audit('sqlite3.connect',(':memory:',))
    gate.importing=False;gate.database_phase=True
    gate.audit('sqlite3.connect',(':memory:',));gate.audit('sqlite3.connect',(gate.uri,))
    with pytest.raises(runtime.Rejected):gate.audit('sqlite3.connect',(str(tmp_path/'fixture.db'),))
    with pytest.raises(runtime.Rejected):gate.audit('sqlite3.connect',('file:///foreign.db?mode=ro',))


@pytest.mark.parametrize('method,url,options',[
    ('POST','https://discord.com/api/v10/guilds/1/channels',{'allow_redirects':False}),
    ('GET','https://discord.com/api/v10/guilds/2/channels',{'allow_redirects':False}),
    ('GET','https://discord.com/api/v10/users/@me/guilds',{'allow_redirects':False}),
    ('GET','https://discord.com/api/v10/channels/1/messages',{'allow_redirects':False}),
    ('GET','https://discord.com/api/v10/users/@me',{'allow_redirects':True}),
    ('GET','https://discord.com/api/v10/users/@me',{'allow_redirects':False,'data':b'body'}),
])
def test_http_mutations_foreign_guilds_and_unbounded_reads_are_denied(tmp_path,method,url,options):
    gate=guard(tmp_path);gate.importing=False;gate.transport=SimpleNamespace(owner=50)
    with pytest.raises(runtime.Rejected):gate.http(method,url,options)


def test_network_is_closed_during_imports_even_inside_an_authorized_read(tmp_path):
    gate=guard(tmp_path);gate.transport=SimpleNamespace(owner=50)
    with pytest.raises(runtime.Rejected):gate.audit('socket.getaddrinfo',('discord.com',443))
    gate.importing=False
    gate.audit('socket.getaddrinfo',('discord.com',443))
    gate.http('GET','https://discord.com/api/v10/guilds/1/roles',{'allow_redirects':False})


@pytest.mark.skipif(os.name!='posix',reason='POSIX private receipt contract')
def test_private_receipt_is_new_mode_0600_and_not_an_application_write(tmp_path):
    ident,path=owner.receipt(tmp_path,{'hostCommit':None,'buildOriginVerified':False})
    assert path.parent==tmp_path and path.name=='games-production-'+ident+'.private-games.json'
    assert path.stat().st_mode & 0o777 == 0o600
    assert 'null' in path.read_text()


def test_normal_attested_enforcement_is_unchanged_and_no_cli_bypass_exists():
    source=Path('tools/game_audit_operator.py').read_text()
    assert "raise ValueError('HOST_REVISION_UNAVAILABLE')" in source
    assert 'legacy' not in source
    source=Path('tools/game_audit_legacy_collect.py').read_text()
    assert 'NORMAL_ATTESTED_ROUTE_REQUIRED' in source
    assert "open('/dev/tty','r'" in source
    assert '--approval' not in source and '--ignore-revision' not in source


def test_verified_skill_runtime_source_is_distinct_from_runtime_database(tmp_path,monkeypatch):
    monkeypatch.setattr(os,'geteuid',lambda:10001,raising=False)
    monkeypatch.setattr(os,'getegid',lambda:10001,raising=False)
    b=binding(tmp_path);sources={p:'pass\n' for p in runtime.FILES}
    monkeypatch.setattr(runtime,'TYPING_SOURCE_SHA',hashlib.sha256(b'pass').hexdigest())
    packet={'binding':b,'ownerApprovalSha256':runtime.approve_hash(b),'sources':sources,'observerCommit':'a'*40,
        'hashes':{p:hashlib.sha256(v.encode()).hexdigest() for p,v in sources.items()},
        'normalizer':'pass','normalizerSha256':hashlib.sha256(b'pass').hexdigest(),
        'supplements':{'typing_extensions':{'source':'pass','sha256':runtime.TYPING_SOURCE_SHA,
            'wheel':'typing_extensions-4.15.0-py3-none-any.whl','wheelSha256':runtime.TYPING_WHEEL_SHA}},
        'verifiedFiles':{p:'a'*64 for p in ('/app/database/db.py','/app/config.py','/app/skill_runtime/runtime/registry.py')}}
    runtime.validate(packet)
    packet['verifiedFiles']['/app/runtime/data/gamerhq.db']='a'*64
    with pytest.raises(runtime.Rejected,match='OBSERVER_SOURCE_REJECTED'):runtime.validate(packet)


def test_query_scope_preserves_channel_dependency_checks_without_member_payloads(tmp_path):
    gate=guard(tmp_path);denied=[]
    with sqlite3.connect(':memory:') as memory:
        memory.executescript('CREATE TABLE games(name TEXT);CREATE TABLE lfg_events(channel_id INTEGER,owner_id INTEGER,description TEXT);'
            'CREATE TABLE members(user_id INTEGER);INSERT INTO lfg_events VALUES(1,2,"private event");')
        memory.set_authorizer(runtime.query_authorizer(gate,denied,sqlite3))
        assert memory.execute('SELECT 1 FROM lfg_events WHERE channel_id=1').fetchone()==(1,)
        assert memory.execute('PRAGMA integrity_check').fetchone()==('ok',)
        for query in ('SELECT owner_id FROM lfg_events','SELECT description FROM lfg_events',
                      'SELECT * FROM members','INSERT INTO games VALUES("unapproved")'):
            with pytest.raises(sqlite3.DatabaseError):memory.execute(query)
    assert denied and gate.denied
