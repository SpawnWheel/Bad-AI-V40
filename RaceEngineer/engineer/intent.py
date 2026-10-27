"""
Lightweight Driver Intent Classifier.
Categorizes driver radio input into discrete communicative acts
before LLM inference so that jokes, commands, declarations, and questions
receive appropriate, non-contradictory responses.
"""

import re
from enum import Enum
from typing import Tuple, Optional


class DriverIntent(Enum):
    JOKE_REQUEST = "JOKE_REQUEST"
    CASUAL_CONVERSATION = "CASUAL_CONVERSATION"
    DRIVER_DECLARATION = "DRIVER_DECLARATION"          # Driver stating an action: "I'm going to pit"
    DRIVER_COMMAND = "DRIVER_COMMAND"                  # Driver giving order: "Box this lap", "Check the tyres"
    MANDATORY_STOP_DECLARATION = "MANDATORY_STOP_DECLARATION" # Unused legacy enum member
    SITREP_QUERY = "SITREP_QUERY"                      # Comprehensive race situation report
    FUEL_QUERY = "FUEL_QUERY"
    PIT_STRATEGY_QUERY = "PIT_STRATEGY_QUERY"
    GAP_QUERY = "GAP_QUERY"
    POSITION_QUERY = "POSITION_QUERY"
    TYRE_QUERY = "TYRE_QUERY"
    LAP_TIME_QUERY = "LAP_TIME_QUERY"
    LAPS_REMAINING_QUERY = "LAPS_REMAINING_QUERY"
    INTERRUPTED = "INTERRUPTED"                        # Incomplete / cut-off speech
    ACKNOWLEDGEMENT = "ACKNOWLEDGEMENT"                # "Copy", "Roger", "Thanks"
    GENERAL_QUERY = "GENERAL_QUERY"


def classify_driver_intent(transcript: str) -> Tuple[DriverIntent, bool, Optional[str]]:
    """
    Classifies the driver's radio message.
    Returns:
        (intent, requires_heavy_telemetry, guidance_note)
    """
    clean = transcript.strip().lower()

    if not clean:
        return DriverIntent.GENERAL_QUERY, True, None

    # 1. Interrupted / trailing speech
    # e.g., "no I'm going to pit, but who", "how far is the", "what about"
    trailing_words = ("who", "what", "where", "how", "but", "and", "or", "which", "is", "the")
    if (
        clean.endswith("...")
        or clean.endswith(",")
        or any(clean.endswith(f" {w}") for w in trailing_words)
        or (len(clean.split()) <= 4 and clean.split()[-1] in trailing_words)
    ):
        return (
            DriverIntent.INTERRUPTED,
            False,
            "The driver's radio call was cut off or interrupted mid-sentence. Do not attempt to guess an answer; ask them cleanly to repeat the call (e.g. 'Say again mate, lost the second part.')."
        )

    # 2. Jokes and casual humor
    if any(k in clean for k in ["joke", "tell me a joke", "make me laugh", "say something funny"]):
        return (
            DriverIntent.JOKE_REQUEST,
            False,
            "The driver specifically asked for a joke. Deliver a sharp, motorsport-themed joke in your own persona's humour and dialect. DO NOT provide unsolicited fuel, tire, or gap reports unless there is an immediate safety crash warning!"
        )

    # 3. SitRep / Comprehensive Race Update requests
    if any(k in clean for k in [
        "sitrep", "sit rep", "situation report", "race update", "give me an update",
        "full update", "how is the race going", "how's the race going",
        "what's happening in the race", "what is happening in the race"
    ]):
        return (
            DriverIntent.SITREP_QUERY,
            True,
            "The driver is asking for a Situation Report (SitRep). Deliver a rich, punchy 3 to 4 sentence update covering BOTH: (1) Your essential race status (position, gaps & pace vs immediate rivals ahead/behind, and fuel/tyre status), AND (2) The wider race picture (analyze [COMPETITOR TIMING & PACE BOARD] to state who is leading and who looks likely to take the win based on pace/gaps/stops, plus any notable earlier spins/incidents from [RACE STORYLINES & INCIDENT RECOVERY LOG] and how those drivers are doing now)."
        )

    # 4. Driver declaration of intent / action
    # e.g. "I'm going to pit", "I'm pitting", "boxing this lap", "coming in"
    if any(k in clean for k in [
        "i'm going to pit", "i am going to pit", "i'm pitting", "i am pitting",
        "i'm coming in", "i am coming in", "boxing this lap", "i will pit", "pitting this lap"
    ]):
        return (
            DriverIntent.DRIVER_DECLARATION,
            True,
            "The driver is declaring their intent to pit. ACKNOWLEDGE their call cleanly (e.g. 'Copy, boxing this lap, pit crew is ready'). Do NOT reject their call or say 'Negative, stay out' unless it is an illegal pit lane closure."
        )

    # 5. Driver commands
    if clean.startswith("box") or clean.startswith("pit") or clean.startswith("check"):
        return (
            DriverIntent.DRIVER_COMMAND,
            True,
            "The driver has given a radio command. Acknowledge directly and concisely."
        )

    # 6. Acknowledgements
    if clean in ["copy", "copy that", "roger", "understood", "all good", "thanks mate", "cheers", "got it"]:
        return (
            DriverIntent.ACKNOWLEDGEMENT,
            False,
            "The driver is simply acknowledging. Reply with a short acknowledgement or 'All good, keep your head down.'"
        )

    # 7. Fuel specific queries
    if any(k in clean for k in [
        "fuel", "liters", "litres", "splash", "consumption", "burn",
        "make it", "make the end", "reach the end", "finish the race",
        "make the flag", "run out", "enough to finish", "fine on fuel",
        "fuel urgent", "is fuel urgent", "are we good on fuel"
    ]):
        return (
            DriverIntent.FUEL_QUERY,
            True,
            "The driver is asking about FUEL, RANGE, or FINISHING THE RACE. Check [FUEL STATUS — COMPUTED] and Fuel Urgency. If the driver will finish before running out (SURPLUS / NOT URGENT), reassure them that they are fine to the finish and do not need to pit. If there is a DEFICIT / URGENT risk, state the deficit and necessary action."
        )

    # 8. Pit strategy & competitor pit stop queries
    if any(k in clean for k in [
        "pit window", "when should i pit", "when do we pit", "pit lap", "strategy", "should i pit",
        "done their stops", "done their pit", "has the field pitted", "who has pitted", "have they pitted",
        "field pitted", "anyone pitted"
    ]):
        return (
            DriverIntent.PIT_STRATEGY_QUERY,
            True,
            "The driver is asking about PIT STRATEGY or COMPETITOR PIT STOPS. For the driver's own strategy, quote [PIT STRATEGY — AUTHORITATIVE] based purely on fuel and tyres (never claim a mandatory pit stop). If asking whether the field or rivals have pitted, report from 'Cars Pitted' in [GRID SUMMARY] and the 'Stops' column in [COMPETITOR TIMING & PACE BOARD]."
        )

    # 9. Gaps & Rivals queries
    if any(k in clean for k in ["gap", "how far", "ahead", "behind", "closing", "catching", "leader"]):
        return (
            DriverIntent.GAP_QUERY,
            True,
            "The driver is asking about timing gaps or competitors. Answer directly using the Competitor Timing Board."
        )

    # 10. Position queries
    if any(k in clean for k in ["position", "what place", "where am i", "p what", "what p"]):
        return (
            DriverIntent.POSITION_QUERY,
            True,
            "The driver is asking for current race position. State position directly first."
        )

    # 11. Tyre queries
    if any(k in clean for k in ["tyre", "tire", "wear", "temps", "temperatures", "pressures", "grip"]):
        return (
            DriverIntent.TYRE_QUERY,
            True,
            "The driver is asking about tyre health and temperatures. Report on the limiting corner and life percentage."
        )

    # 12. Lap count queries
    if any(k in clean for k in ["how many laps", "laps left", "laps remaining", "laps to go", "how long left"]):
        return (
            DriverIntent.LAPS_REMAINING_QUERY,
            True,
            "The driver is asking about remaining race distance. State remaining laps or clock directly."
        )

    return DriverIntent.GENERAL_QUERY, True, None