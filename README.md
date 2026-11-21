# Mech Royale

SophiaOmri's entry for the [Builders Buildathon](https://www.joinbuilders.org/buildathon/tracks/builders). The bot plans routes from the official arena geometry, checks early weapon detours against the time needed to reach the next circle, and leads moving opponents.

Build with `docker build --platform linux/amd64 -t mech-royale .`. The official runner mounts the arena at `/arena` and communicates over the published Mech Royale JSON Lines protocol. Use the official kit's `check/check.py` for conformance.

The entry code is MIT-licensed. It uses the organizer-provided Python `mechfight.py` wrapper from the official starter kit.
