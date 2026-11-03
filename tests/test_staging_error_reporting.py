import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.staging_dispatch import base_environment,buildx_state

def test_buildx_state_is_separate_from_readonly_docker_trust_config():
    with patch('tools.staging_dispatch.os.geteuid',return_value=1002,create=True),patch('tools.staging_dispatch.buildx_state',return_value='/opt/gamerhq-staging/worker/buildx'):
        value=base_environment()
    assert Path(value['DOCKER_CONFIG']).as_posix()=='/etc/gamerhq-staging/docker-client'
    assert value['BUILDX_CONFIG']=='/opt/gamerhq-staging/worker/buildx'
    assert value['DOCKER_HOST']=='unix:///run/user/1002/docker.sock'
    assert not {'DISCORD_TOKEN','GAMERHQ_SKILL_RELEASE_TOKEN','GAMERHQ_STAGING_WEB_API_SECRET'} & value.keys()

@pytest.mark.skipif(os.name!='posix',reason='Linux ownership and permission boundary')
def test_buildx_state_rejects_redirected_or_shared_paths(tmp_path):
    parent=tmp_path/'worker';parent.mkdir(mode=0o700)
    path=parent/'buildx';path.mkdir(mode=0o700);uid=parent.stat().st_uid
    assert buildx_state(path,uid)==str(path)
    inode=path.stat().st_ino
    assert buildx_state(path,uid)==str(path) and path.stat().st_ino==inode
    path.chmod(0o755)
    with pytest.raises(PermissionError):buildx_state(path,uid)
    path.chmod(0o700);path.rmdir();path.symlink_to(tmp_path,target_is_directory=True)
    with pytest.raises(PermissionError):buildx_state(path,uid)

