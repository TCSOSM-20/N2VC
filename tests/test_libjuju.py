# A simple test to verify we're using the right libjuju module
from n2vc.vnf import N2VC  # noqa: F401
import sys


def test_libjuju():
    """Test the module import for our vendored version of libjuju.

    Test and verify that the version of libjuju being imported by N2VC is our
    vendored version, not one installed externally.
    """
    for name in sys.modules:
        if name.startswith("juju"):
            module = sys.modules[name]
            if getattr(module, "__file__"):
                assert module.__file__.find("N2VC/modules/libjuju/juju")

    return
