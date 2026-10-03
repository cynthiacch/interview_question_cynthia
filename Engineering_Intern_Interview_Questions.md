## Introduction

Thank you for your interest in the Nucleus Security Engineering
internship program! This document contains 2 primary interview
challenges for you. The first is a code review question, and the second
is a coding challenge.

For both, we absolutely encourage the use of AI. If you do use AI, we
would like for you to share your prompts and then answer the follow-up
questions about how you thought through your prompts.

We know this time of the year is crazy for college students and that
your time is very valuable. Please try not to spend more than about 1
total hour collectively on this.

------------------------------------------------------------------------

## Contents

-   Introduction
-   Code Review (10 minutes)
    -   Task
    -   PHP
    -   Python
    -   Code comments
    -   Follow-up Questions
-   Coding Challenge (\~50 minutes)
    -   Exercise
    -   Follow-up questions
-   Delivery

------------------------------------------------------------------------

# Code Review (10 minutes)

You are welcome and encouraged to use AI for this section. If you do,
please provide your prompts and answer the questions in the follow-up
section.

## Task

Your colleague or team member was given the following task:

1.  Add a `/webhook` endpoint to receive vendor events about users who
    are vendors.

2.  Input data will look like:

    ``` json
    {"email":"a@b.com","role":"admin","metadata":{"source":"vendor"}}
    ```

3.  Verify signature header `X-Signature`.

4.  Parse JSON and upsert the user data.

5.  Store the raw payload for audit/debug.

They have opened a PR with the code below. Review the code and comment
on any issues you find.

**Note:** Both the PHP and Python do the same thing. You can choose to
review whichever one you want. It is not intended for you to review
both.

------------------------------------------------------------------------

## PHP

``` php
<?php
// webhook.php
require_once "db.php"; // provides $pdo (PDO instance)

// Config (dev defaults)
$WEBHOOK_SECRET = getenv("WEBHOOK_SECRET") ?: "dev-secret";
$DB_AUDIT_ENABLED = getenv("AUDIT_ENABLED") ?: "true";

function verify_signature($sig, $body, $secret) {
    // Vendor docs: SHA256(secret + body)
    $expected = hash("sha256", $secret . $body);
    return $expected == $sig; // simple compare
}

$method = $_SERVER["REQUEST_METHOD"] ?? "GET";
$path = parse_url($_SERVER["REQUEST_URI"], PHP_URL_PATH);

// Basic routing
if ($method !== "POST" || $path !== "/webhook") {
    http_response_code(404);
    echo "not found";
    exit;
}

$raw = file_get_contents("php://input"); // raw body string
$sig = $_SERVER["HTTP_X_SIGNATURE"] ?? "";

if (!verify_signature($sig, $raw, $WEBHOOK_SECRET)) {
    http_response_code(401);
    echo "bad sig";
    exit;
}

// Decode JSON
$payload = json_decode($raw, true);
$email = $payload["email"] ?? "";
$role = $payload["role"] ?? "user";

// Store raw payload for auditing / debugging
if ($DB_AUDIT_ENABLED) {
    $pdo->exec("INSERT INTO webhook_audit(email, raw_json) VALUES ('$email', '$raw')");
}

// Upsert user (simple)
$pdo->exec("INSERT INTO users(email, role) VALUES('$email', '$role')");

echo "ok";
```

------------------------------------------------------------------------

## Python

``` python
# app.py
import os
import json
import sqlite3
import hashlib
from flask import Flask, request

app = Flask(__name__)
DB_PATH = os.getenv("DB_PATH", "/tmp/app.db")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "dev-secret")  # default for dev

def get_db():
    return sqlite3.connect(DB_PATH)

def verify(sig, body: bytes) -> bool:
    # Vendor docs: SHA256(secret + body)
    expected = hashlib.sha256(
        (WEBHOOK_SECRET + body.decode("utf-8")).encode("utf-8")
    ).hexdigest()
    return expected == sig  # simple compare

@app.post("/webhook")
def webhook():
    raw = request.data  # bytes
    sig = request.headers.get("X-Signature", "")

    if not verify(sig, raw):
        return ("bad sig", 401)

    payload = json.loads(raw.decode("utf-8"))

    # Example payload:
    # {"email":"a@b.com","role":"admin","metadata":{"source":"vendor"}}
    email = payload.get("email", "")
    role = payload.get("role", "user")

    db = get_db()
    cur = db.cursor()

    # Store raw payload for auditing / debugging
    cur.execute(
        f"INSERT INTO webhook_audit(email, raw_json) VALUES ('{email}', '{raw.decode('utf-8')}')"
    )

    # Upsert user
    cur.execute(
        f"INSERT INTO users(email, role) VALUES('{email}', '{role}')"
    )

    db.commit()

    return ("ok", 200)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
```

------------------------------------------------------------------------

## Code comments

Put your code comments here. For comments on specific lines, please
include the line number. Feel free to comment on the general task as
well.

---

## Code comments (Python / app.py)

**Summary:** This PR shouldn't be merged as-is. It has a SQL injection vulnerability, an unsafe signature-verification design, insufficient input validation, allows the vendor to assign privileged roles, and doesn't actually perform an upsert. Details below, roughly in order of severity.

### Critical

**Lines 42 and 47: SQL injection.**  
Both queries construct SQL using f-strings, inserting `email`, `role`, and the raw body directly into the query. Anyone who can produce a validly signed payload can potentially inject SQL. Legitimate data can also break the query; for example, an email such as `o'brien@x.com` can cause the statement to fail.

Use parameterized queries instead:

`cur.execute("INSERT INTO users(email, role) VALUES (?, ?)", (email, role))`

The audit insert should be parameterized in the same way.

**Lines 35 and 47: the vendor controls user roles.**  
`role` is taken directly from the payload and written to the database. The example payload even contains `"role":"admin"`. An external vendor should not be able to create administrators in our system unless that is explicitly intended.

Validate `role` against an allowlist of roles the vendor is permitted to assign, or ignore the supplied role and assign a fixed vendor role.

Also consider validating `metadata.source` if this endpoint is specifically intended for vendor events.

**Lines 17–19: non-HMAC signature scheme.**  
`SHA256(secret + body)` is not an HMAC construction and is generally not recommended for message authentication because Merkle–Damgård length-extension attacks apply to this form of hashing.

If the vendor supports it, use HMAC-SHA256 instead:

`hmac.new(secret, body, hashlib.sha256).hexdigest()`

If the vendor requires the documented `SHA256(secret + body)` scheme, we should confirm that requirement and understand the associated security limitations rather than changing the protocol unilaterally.

**Line 20: timing-unsafe comparison.**  
`==` is not the appropriate comparison for cryptographic signatures. Use `hmac.compare_digest(expected, sig)`.

### High

**Line 10: hardcoded fallback secret.**  
If `WEBHOOK_SECRET` isn't configured in production, the application silently uses `"dev-secret"`. Anyone who knows the source code could then generate valid signatures.

The application should fail closed when the secret is missing rather than using a known default.

**Line 47: this isn't an upsert.**  
This is a plain `INSERT`. Repeat events for the same user will either create duplicate rows or fail if there is a unique constraint.

Use the database's actual upsert mechanism, for example:

`INSERT ... ON CONFLICT(email) DO UPDATE SET role = excluded.role`

and make sure `email` has an appropriate unique constraint.

**No replay protection.**  
A captured, validly signed request can potentially be submitted repeatedly. If the vendor provides an event ID, store it and make it unique. Alternatively, use a signed timestamp and reject requests outside an acceptable time window, if supported by the vendor protocol.

**Lines 30 and 34–35: insufficient input validation and error handling.**  
Malformed JSON causes `json.loads()` to raise and results in a server error. A valid JSON array would also cause `.get()` to raise `AttributeError`.

A missing email currently becomes `""` and can be inserted into the database.

The endpoint should validate that the payload is an object, require the required fields, validate their types/values, and return `400` for invalid input.

**Line 18: decoding before signature verification.**  
The signature should be calculated over the exact raw bytes received. The current implementation decodes the request body to UTF-8 before hashing it.

Hash the raw `bytes` directly. JSON decoding should happen only after signature verification succeeds.

### Medium

**Lines 37–50: connection/error handling.**  
The database connection is never explicitly closed, and there is no rollback handling if an exception occurs.

Use a context manager or explicit `try`/`finally` handling so connections are reliably closed and failed transactions are rolled back.

**Lines 41–50: audit and user writes are coupled.**  
The audit record and user update are in the same transaction. If processing fails after the audit insert, the audit record is rolled back too.

Consider whether the audit record should independently record that the event was received and whether processing succeeded or failed. This is particularly useful for debugging failed webhook processing.

**Audit table contains PII.**  
The raw payload contains the user's email and potentially other vendor-supplied data. Make sure the audit data has appropriate access controls and a retention policy.

**Line 24: no request size limit.**  
There is no explicit request-size limit. Configure a maximum request size so an unexpectedly large request cannot consume excessive memory/resources.

### Low / General

**Line 9: `/tmp/app.db` as the default database location.**  
Using `/tmp` as the default means the database may disappear after a reboot or cleanup, and it may not be an appropriate location for application data.

**Line 55: Flask development server.**  
`app.run(host="0.0.0.0")` uses Flask's development server and exposes it on all interfaces. Production deployments should use an appropriate WSGI server and deployment configuration.

**No tests.**  
At minimum, add tests covering:

- valid signature
- invalid signature
- malformed JSON
- missing/invalid required fields
- disallowed roles
- SQL-injection-style input
- repeated events / upsert behavior
- oversized requests, if applicable

**No logging/observability.**  
There is no useful application logging around rejected requests or processing failures. Some structured logging would make webhook failures much easier to diagnose. Avoid logging secrets or unnecessary raw PII.

---
### Overall

The main blockers are the **SQL injection vulnerability**, **privilege escalation through the `role` field**, **unsafe signature verification**, **lack of input validation**, and the fact that the user operation is **not actually an upsert**.

I would address those before merging, then add tests covering the security and retry/idempotency cases.
------------------------------------------------------------------------


## Follow-up Questions

### 1. Share your prompts and the AI outputs.

**Prompt 1:**  
I gave the AI a few bugs that I had already identified and asked:

> “Is there anything I’m missing? Please check the code for me and be specific about the line and explain your reasoning.”

The AI then provided a detailed code review, identifying additional issues and referencing specific lines of code.

**Prompt 2:**  
I then used the AI's response as input for another AI and asked it to review the findings again:

> “I like the detailed explanations we got, but I would like you to check whether I’m missing anything. Please review the findings and let me know if there are any additional issues.”

The second AI reviewed the first AI's output and helped identify areas that could be improved or clarified.

### 2. For each prompt:
- **What was I hoping for the AI to accomplish?**  
  I wanted the AI to act as a second pair of eyes and identify bugs or security issues that I may have overlooked. I also wanted specific line references and explanations so I could understand why each issue mattered.

- **What did it actually do?**  
  The AI provided a detailed review of the code, identified additional issues I had not initially noticed, and explained the reasoning behind each finding. The second AI then reviewed those findings and helped me make the final review more complete and accurate.

- **Did I have to re-prompt it based on its output?**  
  Yes. Rather than simply accepting the first AI's response, I gave its output to another AI and asked it to check whether anything was missing or needed improvement.

- **If I had to change my approach, why?**  
  I did not significantly change my approach. However, I made a point of having another AI review the first AI's output. This gave me an additional layer of verification and helped reduce the chance of missing an important issue.

------------------------------------------------------------------------

# Coding Challenge (\~50 minutes)

For the below coding exercise, there is no expectation that you will
have a fully working solution. For anything you feel you didn't
accomplish, please let us know in the follow-up section after the
exercise.

## Exercise

Build a calculator web application. It should include a frontend piece
and any backend logic needed to perform the calculations.

You can use any language of your choosing for both the frontend and
backend code.

------------------------------------------------------------------------

## Follow-up questions

1.  How far were you able to get with the exercise?
I finished a working calculator web app and deployed it on Vercel. It has three parts:

- Calculate: an everyday and scientific calculator, similar to the iPhone one. It follows the correct order of operations, uses exact decimal math (0.1 + 0.2 = 0.3), shows how it interpreted your input, and can show the working step by step.
- Graph: a graphing calculator inspired by the TI-84 and Desmos, with up to four functions, window settings, zoom, pan and trace.
- Convert: currency (with live exchange rates), everyday units like inches to cm, and shoe sizes (US, UK, EU and JP).
The backend is Python, the frontend is HTML/CSS/JavaScript, and there are 48 automated tests.


2.  What challenges did you encounter in the process?
- Graphing was the hardest part. At first, sin(x) and cos(x) looked like flat lines. I compared our graph with Desmos and found the math was right, but the graph was using degrees instead of radians. Over −10 to 10, that's only sin(−10°) to sin(10°). We gave the graph its own angle setting that defaults to radians. Drawing tan(x) without fake vertical lines at the asymptotes also took some thought.
- Shoe sizes can't be exact. There's no single standard, and every brand uses a slightly different chart, so the converter gives an estimate and tells the user to check the brand's chart.
- Deployment: the first Vercel deploy showed a 404 because Vercel was serving the wrong folder. Fixing it meant understanding how Vercel serves files and runs Python functions.
- Currency rates come from an outside service, so I had to decide what happens when it's down: show the last rates it loaded, or a clear error, never made-up numbers.

3.  If you were given unlimited time, what additional functionality
    would you include?
- More advanced functions, using the TI-84 as a reference: a table view for graphs, statistics (mean, standard deviation, regression), and factorial/nCr.
- Different calculator versions for different needs:
- Finance: loan repayments, compound interest, currency
- Discrete math: mod, gcd, binary/hex conversion, nCr
- Students: step-by-step working for algebra
- Automated browser tests (for example, with Playwright). The interface was only checked manually with screenshots.

4.  If you used AI, please include all of your prompts and answer the
    following questions:
    -   What did the AI do well? 
It was good at discussing the problem with me before building: what makes a calculator good or bad, how big the scope should be, and whether to use Vercel or Railway. It explained problems clearly, such as why the Vercel link showed a 404. It also checked its own work by running the tests and taking screenshots of the app.

    -   What did the AI do poorly?
    - It didn't notice that the graph used degrees, which made sin(x) - look flat. I found that myself by comparing with Desmos.
    - The code review I drafted with a different AI chat still had some of that AI's chat text in it, and two technical points were inaccurate.
    - It started fixing code before we had agreed on a plan. 
    -   For the places it did poorly, how did you change your approach
        to your prompts to improve its output?
        I told it to discuss before building, and made decisions myself before letting it write code.
    - I used screenshots as references instead of describing things: my phone's calculator for the scientific keys, Desmos for the graph, and Vercel for the deploy error.
    - I asked it to self-check (for example, "self check the calculation problem") and to explain how it tested things.
    - I used one AI to review another AI's work. Claude checked the code review and found the leftover text and the inaccurate points.


------------------------------------------------------------------------

# Delivery

Please reply to the email you received with:

1.  Answers to any follow-up above.
2.  Any questions or thoughts you had on the exercise.
3.  A link to a public GitHub repository including your answer to the
    coding challenge.
    -   If we can't get to the repository, we won't be able to consider
        your answer to the coding challenge.
