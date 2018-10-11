"""
Test N2VC's ssh key generation
"""
import os
import pytest
from . import base
import tempfile


@pytest.mark.asyncio
async def test_ssh_keygen(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdirname:
        monkeypatch.setitem(os.environ, "HOME", tmpdirname)

        client = base.get_n2vc()

        public_key = await client.GetPublicKey()
        assert len(public_key)
