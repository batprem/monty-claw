from monty_claw.config import Settings
from monty_claw.storage.base import BlobStorage
from monty_claw.storage.local import LocalStorage


def get_storage(settings: Settings) -> BlobStorage:
    if settings.storage_backend == 'gcs':
        from monty_claw.storage.gcs import GcsStorage

        if not settings.gcs_bucket:
            raise ValueError('STORAGE_BACKEND=gcs requires GCS_BUCKET')
        return GcsStorage(settings.gcs_bucket)
    return LocalStorage(settings.local_storage_dir)


__all__ = ['BlobStorage', 'LocalStorage', 'get_storage']
