#  Copyright 2019 Canonical Ltd.

#  Licensed under the Apache License, Version 2.0 (the "License");
#  you may not use this file except in compliance with the License.
#  You may obtain a copy of the License at

#      http://www.apache.org/licenses/LICENSE-2.0

#      Unless required by applicable law or agreed to in writing, software
#      distributed under the License is distributed on an "AS IS" BASIS,
#      WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#      See the License for the specific language governing permissions and
#      limitations under the License.

"""
Test N2VC's ssh key generation
"""
import n2vc
import pytest
from . import base
import uuid


@pytest.mark.asyncio
async def test_model_create():
    """Test the creation of a new model."""
    client = base.get_n2vc()

    model_name = "test-{}".format(
        uuid.uuid4().hex[-4:],
    )

    pytest.assume(await client.CreateNetworkService(model_name))
    pytest.assume(await client.DestroyNetworkService(model_name))
    pytest.assume(await client.logout())


@pytest.mark.asyncio
async def test_destroy_non_existing_network_service():
    """Destroy a model that doesn't exist."""

    client = base.get_n2vc()

    model_name = "test-{}".format(
        uuid.uuid4().hex[-4:],
    )

    with pytest.raises(n2vc.vnf.NetworkServiceDoesNotExist):
        pytest.assume(await client.DestroyNetworkService(model_name))

    pytest.assume(await client.logout())


@pytest.mark.asyncio
async def test_model_create_duplicate():
    """Create a new model, and try to create the same model."""
    client = base.get_n2vc()

    model_name = "test-{}".format(
        uuid.uuid4().hex[-4:],
    )

    # Try to recreate bug 628
    for x in range(0, 1000):
        model = await client.get_model(model_name)
        pytest.assume(model)

    pytest.assume(await client.DestroyNetworkService(model_name))
    pytest.assume(await client.logout())
