# Project Floaty roadmap

Floaty (a working codename) is a palm-sized AI agent with a face, built in public by the 0G DevRel team on
M5Stack StackChan hardware. Its brain runs on a computer at home; the cloud thinking goes through one
OpenAI-compatible API on 0G Private Computer, and live voice uses OpenAI realtime.

The code opens one piece at a time. This list is what is open, what comes next, and what comes later.

## Open now

- [x] Dashboard tab: robot link, usage, approvals waiting, models, errors (demo data)
- [x] Memory tab: facts, documents and past conversations, with search (demo data)
- [x] Sensors tab: live mic, spectrogram, transcripts, motion, proximity, touch, servos, power, events
- [x] Telemetry protocol (PROTOCOL.md) and a fake robot, so it all runs without hardware
- [x] Ask Floaty on the Memory tab: answers from memory on 0G Private Computer, private mode (Intel TDX enclave)

## Next

- [ ] More dashboard tabs, one at a time: Faces, Tools, Crons (routines), Notifications, Webhooks, Wake, Router, Tables
- [ ] Wake word as a setting: every builder picks their own, and the community picks Floaty's real name
- [ ] Sealed thinking in the brain: background and routine jobs in 0G Private Computer's private mode, with every
      reply's signature checked at home
- [ ] Hearing eval: the clip sets and scoring script behind the speech gate and the second wake listener
- [ ] The brain: the agent service (voice, tools, memory, routines, background tasks, approvals)
- [ ] The firmware: the StackChan fork with the telemetry module, the always-on listener and the faces,
      keeping every upstream license

## Later

- [ ] A badge on its face showing which model answered
- [ ] A visible mute that stops all listening
- [ ] More expressions and head moves
- [ ] A build guide: parts list and setup from zero
