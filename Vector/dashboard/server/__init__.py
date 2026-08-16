"""
AP_FLAKE8_CLEAN
Vector dashboard server package.

``config``      vehicle description loaded from Vector/config/vector.json
``kinematics``  pure gimbal geometry, no state and no link
``link``        MAVLink connection, telemetry snapshots, output commands
``controller``  host-side bench stabiliser
``protocol``    WebSocket command dispatch
``app``         Starlette application serving the built UI and the socket
"""
