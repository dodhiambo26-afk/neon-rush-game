"""Deterministic tournament race simulation.

This module is the scoring authority for weekly and monthly races. Keep its
rules in lockstep with the tournament simulation in index.html.
"""
from __future__ import annotations

VIRTUAL_WIDTH = 390
VIRTUAL_HEIGHT = 844
PLAYER_Y = VIRTUAL_HEIGHT * 0.78
MAX_TRAFFIC = 4
MAX_PICKUPS = 4
MAX_TICKS = 60 * 60 * 2


class XorShift32:
    def __init__(self, seed: int):
        self.state = int(seed) & 0xFFFFFFFF
        if self.state == 0:
            self.state = 0x6D2B79F5

    def random(self) -> float:
        x = self.state
        x ^= (x << 13) & 0xFFFFFFFF
        x ^= x >> 17
        x ^= (x << 5) & 0xFFFFFFFF
        self.state = x & 0xFFFFFFFF
        return self.state / 4294967296


def simulate_race(seed: int, inputs: list[dict], ticks: int) -> dict:
    """Replay lane changes and return the only score eligible for storage.

    Input records are {"tick": positive_int, "direction": -1|1}; records
    are applied in their submitted order immediately before that simulation
    tick. The client cannot submit an arbitrary score as an authority.
    """
    if not isinstance(ticks, int) or ticks < 1 or ticks > MAX_TICKS:
        raise ValueError("Invalid race length.")
    if not isinstance(inputs, list) or len(inputs) > 10000:
        raise ValueError("Invalid steering history.")

    clean_inputs = []
    previous_tick = 0
    for item in inputs:
        if not isinstance(item, dict):
            raise ValueError("Invalid steering event.")
        tick = item.get("tick")
        direction = item.get("direction")
        if (not isinstance(tick, int) or isinstance(tick, bool)
                or tick < 1 or tick > ticks
                or tick < previous_tick
                or direction not in (-1, 1)):
            raise ValueError("Invalid steering history.")
        clean_inputs.append((tick, direction))
        previous_tick = tick

    rng = XorShift32(seed)
    lane = 1
    score = 0
    coins = 0
    lives = 3
    traffic = []
    pickups = []
    road_offset = 0.0
    traffic_timer = 0.0
    coin_timer = 0.0
    distance_timer = 0.0
    input_index = 0
    completed_ticks = 0
    factor = 0.82  # Tournament difficulty is fixed to the current EASY rules.

    for current_tick in range(1, ticks + 1):
        while input_index < len(clean_inputs) and clean_inputs[input_index][0] == current_tick:
            lane = max(0, min(2, lane + clean_inputs[input_index][1]))
            input_index += 1

        speed = min(10.0, (4.2 + score / 180.0) * factor)
        road_offset += speed
        traffic_timer += 1
        coin_timer += 1
        distance_timer += 1

        if distance_timer >= 30:
            score += 1
            distance_timer = 0

        traffic_interval = max(30.0, (68.0 - score / 35.0) / factor)
        if traffic_timer > traffic_interval and len(traffic) < MAX_TRAFFIC:
            traffic.append({"lane": int(rng.random() * 3), "y": -80.0})
            rng.random()  # Vehicle colour consumes the second client RNG value.
            traffic_timer = 0

        coin_interval = max(20.0, 42.0 - score / 80.0)
        if coin_timer > coin_interval and len(pickups) < MAX_PICKUPS:
            roll = rng.random()
            kind = "heart" if roll < 0.12 else ("diamond" if roll < 0.30 else "coin")
            pickups.append({"type": kind, "lane": int(rng.random() * 3), "y": -35.0})
            coin_timer = 0

        for index in range(len(traffic) - 1, -1, -1):
            car = traffic[index]
            car["y"] += speed
            if car["lane"] == lane and abs(car["y"] - PLAYER_Y) < 62 * 0.75:
                traffic.pop(index)
                lives -= 1
                if lives <= 0:
                    completed_ticks = current_tick
                    return {"score": score, "coins": coins, "lives": lives,
                            "ticks": completed_ticks, "ended": True}
                continue
            if car["y"] > VIRTUAL_HEIGHT + 80:
                traffic.pop(index)

        for index in range(len(pickups) - 1, -1, -1):
            pickup = pickups[index]
            pickup["y"] += speed
            if pickup["lane"] == lane and abs(pickup["y"] - PLAYER_Y) < 38:
                pickups.pop(index)
                if pickup["type"] == "diamond":
                    score += 100
                elif pickup["type"] == "heart":
                    score += 50
                    lives = min(3, lives + 1)
                else:
                    coins += 1
                    score += 10
                continue
            if pickup["y"] > VIRTUAL_HEIGHT + 35:
                pickups.pop(index)

        completed_ticks = current_tick

    return {"score": score, "coins": coins, "lives": lives,
            "ticks": completed_ticks, "ended": False}
