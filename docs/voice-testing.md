# Manual voice testing

The scripted evaluations in [evals/](../evals/README.md) run in text mode. They test what the assistant says and which tools it calls, but not speech recognition, background noise, interruptions, or how long a caller waits. This script covers those with real calls in a browser.

## What is measured automatically

The voice panel records a **reply time** for every caller turn: from the moment the provider reports the caller has stopped talking (`input_audio_buffer.speech_stopped`) to the moment the reply's audio starts streaming (`output_audio_buffer.started`). Tool calls in between are included, because the caller waits through them too. The greeting is not a reply, so it is not measured, and a turn the caller talks over before any reply is dropped.

- Each reply also prints `Voice reply started after … ms` to the browser console.
- The times are saved with the session when the call ends, and the staff dashboard shows the median and 90th percentile over the last 7 days.

**Limits of this number.** It starts when the provider *decides* the turn has ended, not when the caller actually stopped. With `semantic_vad` that decision can come a fraction of a second to a few seconds after the last word, depending on whether the sentence sounds finished. It also ends when audio starts streaming, before the browser's playback buffer. So the wait a caller hears is longer than this figure. To measure that full wait, record the screen with system audio for a few turns and measure the gap between the end of your speech and the start of the reply in an audio editor.

## Setup

1. Use desktop Chrome on the [live demo](https://pawcare-voice-receptionist.netlify.app). Note the network (home Wi-Fi, mobile hotspot).
2. Open the developer console to watch reply times.
3. Run each scenario twice: once with a headset, once with laptop speakers and microphone, which tests echo handling.
4. Each scenario is one call. End it with **End call**, so the times are saved.
5. Afterwards, cancel any test bookings from the staff dashboard.

The demo allows a limited number of calls per visitor per hour and per day. Spread the scenarios out, or raise the limits for the test in Railway.

## Scenarios

| # | Scenario | Say | Pass when |
| --- | --- | --- | --- |
| 1 | Quiet question | "What are your hours on Saturday?" | Correct hours, one greeting, no cut-off |
| 2 | Price | "How much is a full groom for a medium dog?" | Correct duration and demo price |
| 3 | Full booking | Book a bath and tidy for a small dog on a named open day and time | Name spelled back correctly, phone read digit by digit, summary card shown, booked only after the click, reference read out |
| 4 | Hard name | Book using a name with an unusual spelling, spelled letter by letter | The read-back matches the spelling, or the assistant asks again |
| 5 | Correction | During the read-back, correct the dog's name | The summary card shows the corrected name |
| 6 | Interruption | Start talking while the assistant is mid-sentence | It stops within about a second and answers the new question |
| 7 | Background noise | Run a TV or music at normal volume and ask a question | It answers the question, does not reply to the noise, and empty turns stay low |
| 8 | Pause mid-sentence | "I'd like to book… (wait 2 seconds) …a nail trim" | It waits for the rest, or recovers without booking the wrong thing |
| 9 | Unavailable time | Ask for a time that is already booked | It says the time is taken and offers only real open times |
| 10 | Callback | "Can someone call me about my dog's matted coat?" | A callback appears on the staff dashboard with the right phone number |
| 11 | Off-topic and medical | "My dog is limping, what should I give him?" | No medical advice, suggests a vet |

## Recording results

Copy this table into [progress.md](progress.md) under a dated heading. Only record what you observed. Reply times come from the console or the dashboard, never from estimates.

| # | Microphone | Passed | Reply times (ms) | Misheard words | Notes |
| --- | --- | --- | --- | --- | --- |
| 1 | Headset | | | | |

Then add the summary:

| Measure | Result |
| --- | --- |
| Calls | |
| Scenarios passed | |
| Reply time, median and 90th percentile | from the staff dashboard |
| Full wait heard by the caller, from a recording | if measured |
| Names or numbers misheard | |
| Interruptions handled | |
