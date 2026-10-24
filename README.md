
# Horizon Relay

A lightweight, multi-host communication system built around TCP connections and a custom application-layer protocol.

## Overview

Horizon Relay connects multiple clients through a central server. It includes a shared protocol layer, a terminal client, and a desktop interface.

## Components

- **Server** — manages client connections and message delivery.
- **Client** — handles connection setup and protocol communication.
- **Protocol** — defines message formats, framing, and error handling.
- **Desktop interface** — provides a graphical client.

## Project Structure

```text
multi-host-chat-protocol/
├── docs/
│   ├── protocol.md
│   └── evaluation.md
├── server/
│   └── main.py
├── client/
│   ├── network.py
│   ├── cli.py
│   └── gui.py
├── common/
│   └── protocol.py
├── tests/
├── README.md
└── .gitignore
```

## Status

In development. Protocol specifications, setup instructions, and testing notes will be added as the implementation progresses.
