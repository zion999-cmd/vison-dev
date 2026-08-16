"""
Entity Stats DB — cross-session persistence for Importance Observatory.

NOT memory. NOT value. Just saves entity statistics so we can observe
which entities consistently cause downstream events across sessions.

Phase 7A: observe patterns. No value formula.
"""

import sqlite3
import json
import time
import logging
import os
from typing import Optional, Dict, List

logger = logging.getLogger("EntityStatsDB")


class EntityStatsDB:
    """Lightweight SQLite store for entity cross-session statistics.

    Schema:
        entity_signature TEXT PRIMARY KEY  -- visual sig hash
        class_name TEXT
        role_weight REAL
        seen_total INTEGER
        interaction_total INTEGER
        tracking_count INTEGER
        state_transition_count INTEGER
        cognition_count INTEGER
        speech_count INTEGER
        event_types TEXT                  -- JSON array
        first_seen REAL
        last_seen REAL
        session_count INTEGER
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Default: logs/entity_stats.db
            db_path = os.path.join(os.path.dirname(__file__), "..", "..",
                                   "logs", "entity_stats.db")
        self._db_path = os.path.abspath(db_path)
        self._conn: Optional[sqlite3.Connection] = None

    def open(self):
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS entity_stats (
                entity_signature TEXT PRIMARY KEY,
                class_name TEXT DEFAULT '',
                role_weight REAL DEFAULT 0.2,
                seen_total INTEGER DEFAULT 0,
                interaction_total INTEGER DEFAULT 0,
                tracking_count INTEGER DEFAULT 0,
                state_transition_count INTEGER DEFAULT 0,
                cognition_count INTEGER DEFAULT 0,
                speech_count INTEGER DEFAULT 0,
                event_types TEXT DEFAULT '[]',
                first_seen REAL DEFAULT 0.0,
                last_seen REAL DEFAULT 0.0,
                session_count INTEGER DEFAULT 0
            )
        """)
        self._conn.commit()
        logger.info("EntityStatsDB: opened %s", self._db_path)

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def _sig_key(self, entity) -> str:
        """Derive a stable key from visual signature + class_name."""
        sig = entity.visual_signature
        if sig:
            # Round to 2 decimal places for stability
            parts = [f"{v:.2f}" for v in sig]
            return f"{entity.class_name}_{'_'.join(parts)}"
        return f"{entity.class_name}_{entity.entity_id}"

    def save_entity(self, entity):
        """Upsert entity stats into DB."""
        if not self._conn:
            return
        key = self._sig_key(entity)
        event_json = json.dumps(sorted(entity.event_types))
        now = time.time()

        self._conn.execute("""
            INSERT INTO entity_stats
                (entity_signature, class_name, role_weight,
                 seen_total, interaction_total,
                 tracking_count, state_transition_count,
                 cognition_count, speech_count,
                 event_types, first_seen, last_seen, session_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(entity_signature) DO UPDATE SET
                class_name = excluded.class_name,
                role_weight = excluded.role_weight,
                seen_total = entity_stats.seen_total + excluded.seen_total,
                interaction_total = entity_stats.interaction_total + excluded.interaction_total,
                tracking_count = entity_stats.tracking_count + excluded.tracking_count,
                state_transition_count = entity_stats.state_transition_count + excluded.state_transition_count,
                cognition_count = entity_stats.cognition_count + excluded.cognition_count,
                speech_count = entity_stats.speech_count + excluded.speech_count,
                event_types = excluded.event_types,
                last_seen = excluded.last_seen,
                session_count = entity_stats.session_count + 1
        """, (
            key, entity.class_name or entity.entity_type, entity.role_weight,
            entity.seen_count, entity.interaction_count,
            entity.tracking_count, entity.state_transition_count,
            entity.cognition_trigger_count, entity.speech_related_count,
            event_json, getattr(entity, 'first_seen', now), now,
        ))
        self._conn.commit()

    def save_all(self, entities: List, use_quality_gate: bool = True):
        """Persist stats for entities that pass the quality gate.

        Phase 7B: only ACTIVE entities with sufficient seen_count and confidence
        enter the cross-session stats DB. This removes detection noise from
        the importance signal.
        """
        from runtime.importance.entity_quality import is_valid_for_importance

        saved = 0
        rejected = 0
        for e in entities:
            if use_quality_gate and not is_valid_for_importance(e):
                rejected += 1
                continue
            if e.is_active or e.interaction_count > 0:
                self.save_entity(e)
                saved += 1

        if rejected > 0:
            logger.debug(
                "EntityStatsDB: saved %d, rejected %d (quality gate)",
                saved, rejected,
            )
        else:
            logger.debug("EntityStatsDB: saved %d entities", saved)

    def load_summary(self) -> Dict:
        """Return cross-session summary for observatory."""
        if not self._conn:
            return {}
        rows = self._conn.execute("""
            SELECT class_name, COUNT(*) as n,
                   SUM(seen_total) as seen,
                   SUM(interaction_total) as interactions,
                   SUM(session_count) as sessions
            FROM entity_stats
            GROUP BY class_name
            ORDER BY interactions DESC
        """).fetchall()
        return {
            "by_class": [
                {"class": r[0], "count": r[1], "seen": r[2],
                 "interactions": r[3], "sessions": r[4]}
                for r in rows
            ]
        }
