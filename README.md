# Mech Royale ground router

This entry chooses walking routes through the arena, moves toward the next safe circle, and sidesteps if it gets stuck. It uses the organizer-provided `mechfight.py` wrapper for the bot protocol. [Builders Mech Royale track and starter kit](https://www.joinbuilders.org/buildathon/tracks/builders)

Build from this directory with `docker build -t mech-ground-route .`. The scorer mounts the official map at `/arena`.

The entry code is released under the MIT License.
