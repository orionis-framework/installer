"""Provide an offline storage syntax fixture without cloud activity."""

from orionis.environment import Env
from orionis.foundation.config.filesystems import GCS, S3, Azure, Disks, Local


def configuration():
    """
    Build the storage configuration inspected by offline tests.

    Returns
    -------
    dict
        Default disk and representative storage entities.
    """
    return {
        "default": Env.get("FILESYSTEM_DISK", "local"),
        "disks": Disks(local=Local(), s3=S3(), azure=Azure(), gcs=GCS()),
    }
