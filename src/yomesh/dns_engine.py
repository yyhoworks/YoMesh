"""Choose the authoritative DNS without promoting a migration draft."""


def engine_for(profile):
    if not isinstance(profile, dict):
        return None
    native = profile.get("native_configuration")
    if isinstance(native, dict) and native.get("format") == "mihomo-localdns-v1":
        return "localdns"
    if "mosdns" in profile or native is None:
        return "mosdns"
    return None
