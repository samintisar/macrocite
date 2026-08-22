import hashlib


def signal_set_fingerprint(signal_ids: list[str]) -> str:
    joined = ",".join(sorted(signal_ids))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()
