# Viva questions and short answers

1. **What problem does SmartVision solve?** It detects parking-space occupancy and shares status with a server and live dashboard.
2. **Where does computer vision run?** At the edge in `vision/main.py`, close to the camera, so raw video need not traverse the network.
3. **Why send JSON over HTTP?** Each detection batch is a small independent request with a clear request/response result and easy validation.
4. **Why use WebSocket too?** HTTP is convenient for vision updates; WebSocket keeps a connection open so the server can immediately push changes to dashboards.
5. **What is the role of TCP/IP?** IP routes packets between hosts; TCP provides ordered, reliable transport for HTTP and WebSocket connections.
6. **What does a port identify?** It directs traffic arriving at a host to a service; this backend listens on port 8000 by default.
7. **What is edge computing here?** The camera-side process analyzes frames locally and transmits compact occupancy results instead of full video.
8. **How does the first OpenCV detector work?** It crops each configured polygon, measures edge density, and compares it to a configurable threshold. It is a baseline that needs camera-specific calibration.
9. **What does the simulator demonstrate?** It sends the same heartbeat and parking-update HTTP payloads as the vision process, so the full network and dashboard path works without camera hardware.
10. **How is occupancy stored?** SQLAlchemy models lots, slots, events, active/completed sessions, cameras, alerts, and occupancy snapshots in SQLite.
11. **How is parking duration calculated?** A FREE-to-OCCUPIED transition opens a session; OCCUPIED-to-FREE sets its exit time and elapsed seconds.
12. **How is camera failure detected?** The backend compares the last heartbeat with a configurable timeout and broadcasts CAMERA_STATUS=OFFLINE. Existing occupancy is left stale rather than changed to FREE.
13. **How do you prevent malformed data?** Pydantic validates types, allowed statuses, confidence range, and required fields; the service also verifies slot IDs.
14. **How is the best slot chosen?** The MVP chooses the first confirmed FREE standard slot as a proxy for distance from the entrance. A real layout can use geometric distance and user preferences.
15. **Is the prediction machine learning?** No. It is an explicitly labeled historical average baseline; it needs more snapshots and a richer time-of-day model for useful forecasts.
16. **What is a current limitation?** Edge density is sensitive to camera angle, shadows, and pavement texture. A calibrated detector or YOLO vehicle overlap model would improve robustness.
17. **How can the system scale?** Move SQLite to PostgreSQL, run multiple API workers with a shared pub/sub layer, add authenticated camera identities, and use TLS.
