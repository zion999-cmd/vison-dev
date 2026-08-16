# Vision Perception Runtime — Architecture

## Layer Stack (current)

```
Detection (YOLO + YuNet)
    ↓
Entity Association (HSV signature match)
    ↓
Entity Registry (CANDIDATE → ACTIVE → LOST → FORGOTTEN)
    ↓
┌─────────────────────────────────────────────┐
│ Layer          │ Timescale   │ Question      │
├────────────────┼─────────────┼───────────────┤
│ Interest       │ seconds     │ What changed? │
│ Curiosity      │ minutes     │ Worth revisit?│
│ Familiarity    │ min–hours   │ Seen enough?  │
│ Role           │ innate      │ Who matters?  │
└─────────────────────────────────────────────┘
    ↓
PTZ Revisit (sweep → stay → track → explore)
```

## Curiosity Formula

```
curiosity = interest × uncertainty × freshness × (1 − familiarity) × role − cost
                   ↑              ↑            ↑                ↑
              "what changed"  "how long    "seen too         "innate
                              since seen?"  many times?"      priority"
```

## Key Files

| Module | File | Role |
|--------|------|------|
| Entity | `runtime/interest/entity.py` | Core data: identity, lifecycle, interest, familiarity, role |
| EntityRegistry | `runtime/interest/entity_registry.py` | Detection→Entity association, lifecycle |
| InterestEngine | `runtime/interest/engine.py` | Legacy interest targets + CuriosityQueue scoring |
| RevisitController | `runtime/interest/revisit.py` | PTZ decisions: stay/leave/track/explore |
| AnchorManager | `runtime/interest/anchor.py` | Spatial baseline ("what's normally here?") |
| FamiliarityEngine | `runtime/familiarity/engine.py` | Session habituation (0=new, 1=seen 100×) |
| RoleEngine | `runtime/role/engine.py` | Innate priority weights (person=1.0, chair=0.1) |
| ServoPTZ | `runtime/perception/servo_ptz.py` | Arduino SG90 serial control |

## Verified Behavior (2026-06-18, 2hr test)

- Person consistently outranks furniture via role weight (person fam=0.86 > chair fam=0.00)
- Familiarity distribution stays healthy after 2hr (0.4–0.8 range)
- Entity-driven revisits: 5 selections, 3/5 were person (role=1.0)
- 758 stays, 170 face tracks, 0 crashes
