# Code Review

## Code Comments — Python / `app.py`

**Summary:** This PR shouldn't be merged as-is. It has a SQL injection vulnerability, an unsafe signature-verification design, insufficient input validation, allows the vendor to assign privileged roles, and doesn't actually perform an upsert. Details below, roughly in order of severity.

### Critical

**Lines 39–45: SQL injection.**  
Both queries construct SQL using f-strings, inserting `email`, `role`, and the raw body directly into the query. Anyone who can produce a validly signed payload can potentially inject SQL. Legitimate data can also break the query; for example, an email such as `o'brien@x.com` can cause the statement to fail.

Use parameterized queries instead:

```python
cur.execute(
    "INSERT INTO users(email, role) VALUES (?, ?)",
    (email, role)
)
```

The audit insert should be parameterized in the same way.

**Lines 32–33 and 43–45: the vendor controls user roles.**  
`role` is taken directly from the payload and written to the database. The example payload even contains `"role":"admin"`. An external vendor should not be able to create administrators in our system unless that is explicitly intended.

Validate `role` against an allowlist of roles the vendor is permitted to assign, or ignore the supplied role and assign a fixed vendor role.

Also consider validating `metadata.source` if this endpoint is specifically intended for vendor events.

**Lines 15–19: non-HMAC signature scheme.**  
`SHA256(secret + body)` is not an HMAC construction and is generally not recommended for message authentication. Because this is a custom signing scheme, it should be confirmed against the vendor's actual protocol rather than assumed to be secure.

If the vendor supports it, use HMAC-SHA256 instead:

```python
hmac.new(secret, body, hashlib.sha256).hexdigest()
```

If the vendor requires the documented `SHA256(secret + body)` scheme, we should confirm that requirement and understand the associated security limitations rather than changing the protocol unilaterally.

**Line 19: timing-unsafe comparison.**  
`==` is not the appropriate comparison for cryptographic signatures. Use `hmac.compare_digest(expected, sig)`.

### High

**Line 10: hardcoded fallback secret.**  
If `WEBHOOK_SECRET` isn't configured in production, the application silently uses `"dev-secret"`. Anyone who knows the source code could then generate valid signatures.

The application should fail closed when the secret is missing rather than using a known default.

**Lines 43–45: this isn't an upsert.**  
This is a plain `INSERT`. Repeat events for the same user will either create duplicate rows or fail if there is a unique constraint.

Use the database's actual upsert mechanism, for example:

```sql
INSERT INTO users(email, role)
VALUES (?, ?)
ON CONFLICT(email) DO UPDATE SET role = excluded.role
```

and make sure `email` has an appropriate unique constraint.

**No replay protection.**  
A captured, validly signed request can potentially be submitted repeatedly. If the vendor provides an event ID, store it and make it unique. Alternatively, use a signed timestamp and reject requests outside an acceptable time window, if supported by the vendor protocol.

**Lines 29–33: insufficient input validation and error handling.**  
Malformed JSON causes `json.loads()` to raise and results in a server error. A valid JSON array would also cause `.get()` to raise `AttributeError`.

A missing email currently becomes `""` and can be inserted into the database.

The endpoint should validate that the payload is an object, require the required fields, validate their types and values, and return `400` for invalid input.

**Line 18: preserve the raw bytes for signature verification.**  
The signature should be calculated over the exact raw bytes received. The current implementation decodes the request body to UTF-8 before hashing it.

Hash the raw `bytes` directly. JSON decoding should happen only after signature verification succeeds.

### Medium

**Lines 35–47: connection and error handling.**  
The database connection is never explicitly closed, and there is no rollback handling if an exception occurs.

Use a context manager or explicit `try`/`finally` handling so connections are reliably closed and failed transactions are rolled back.

**Lines 39–47: audit and user writes are coupled.**  
The audit record and user update are in the same transaction. If processing fails after the audit insert, the audit record is rolled back too.

Consider whether the audit record should independently record that the event was received and whether processing succeeded or failed. This is particularly useful for debugging failed webhook processing.

**Audit table contains PII.**  
The raw payload contains the user's email and potentially other vendor-supplied data. Make sure the audit data has appropriate access controls and a retention policy.

**No request size limit.**  
There is no explicit request-size limit. Configure a maximum request size so an unexpectedly large request cannot consume excessive memory or resources.

### Low / General

**Line 9: `/tmp/app.db` as the default database location.**  
Using `/tmp` as the default means the database may disappear after a reboot or cleanup, and it may not be an appropriate location for application data.

**Lines 51–52: Flask development server.**  
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

### Overall

The main blockers are the **SQL injection vulnerability**, **privilege escalation through the `role` field**, **unsafe signature verification**, **lack of input validation**, and the fact that the user operation is **not actually an upsert**.

I would address those before merging, then add tests covering the security and retry/idempotency cases.

---

# Follow-up Questions

## 1. Share your prompts and the AI outputs.

### Prompt 1: Code Review

I gave the AI a few bugs that I had already identified and asked:

> “Is there anything I’m missing? Please check the code for me and be specific about the line and explain your reasoning.”

The AI then provided a detailed code review, identifying additional issues and referencing specific lines of code.

### Prompt 2: Reviewing the AI's Findings

I then used the AI's response as input for another AI and asked it to review the findings again:

> “I like the detailed explanations we got, but I would like you to check whether I’m missing anything. Please review the findings and let me know if there are any additional issues.”

The second AI reviewed the first AI's output and helped identify areas that could be improved or clarified.

### For each prompt:

**What was I hoping for the AI to accomplish?**

I wanted the AI to act as a second pair of eyes and identify bugs or security issues that I may have overlooked. I also wanted specific line references and explanations so I could understand why each issue mattered.

**What did it actually do?**

The AI provided a detailed review of the code, identified additional issues I had not initially noticed, and explained the reasoning behind each finding. The second AI then reviewed those findings and helped me make the final review more complete and accurate.

**Did I have to re-prompt it based on its output?**

Yes. Rather than simply accepting the first AI's response, I gave its output to another AI and asked it to check whether anything was missing or needed improvement.

**If I had to change my approach, why?**

I did not significantly change my approach. However, I made a point of having another AI review the first AI's output. This gave me an additional layer of verification and helped reduce the chance of missing an important issue.

---

# Coding Challenge

## 1. How far were you able to get with the exercise?

I finished a working calculator web app and deployed it on Vercel. It has three parts:

- **Calculate:** an everyday and scientific calculator, similar to the iPhone one. It follows the correct order of operations, uses exact decimal math (0.1 + 0.2 = 0.3), shows how it interpreted your input, and can show the working step by step.
- **Graph:** a graphing calculator inspired by the TI-84 and Desmos, with up to four functions, window settings, zoom, pan and trace.
- **Convert:** currency (with live exchange rates), everyday units like inches to cm, and shoe sizes (US, UK, EU and JP).

The backend is Python, the frontend is HTML/CSS/JavaScript, and there are 48 automated tests.

## 2. What challenges did you encounter in the process?

- **Graphing was the hardest part.** At first, `sin(x)` and `cos(x)` looked like flat lines. I compared our graph with Desmos and found the math was right, but the graph was using degrees instead of radians. Over −10 to 10, that's only `sin(−10°)` to `sin(10°)`. We gave the graph its own angle setting that defaults to radians. Drawing `tan(x)` without fake vertical lines at the asymptotes also took some thought.
- **Shoe sizes can't be exact.** There's no single standard, and every brand uses a slightly different chart, so the converter gives an estimate and tells the user to check the brand's chart.
- **Deployment:** The first Vercel deploy showed a 404 because Vercel was serving the wrong folder. Fixing it meant understanding how Vercel serves files and runs Python functions.
- **Currency rates:** Currency rates come from an outside service, so I had to decide what happens when it's down: show the last rates it loaded, or a clear error, never made-up numbers.

## 3. If you were given unlimited time, what additional functionality would you include?

- More advanced functions, using the TI-84 as a reference: a table view for graphs, statistics (mean, standard deviation, regression), and factorial/nCr.
- Different calculator versions for different needs:
  - Finance: loan repayments, compound interest, currency
  - Discrete math: mod, gcd, binary/hex conversion, nCr
  - Students: step-by-step working for algebra
- Automated browser tests (for example, with Playwright). The interface was only checked manually with screenshots.

## 4. If you used AI, please include all of your prompts and answer the following questions.

### Prompt 1: Project Planning

I want to build a calculator website/app.

Before we start coding, help me plan the project properly.

First, think about:

- Who the calculator is for
- What types of calculators we should include
- What the core MVP should contain
- What features would make it genuinely useful
- What would make it feel polished compared with existing calculator websites
- What should be saved for later instead of building immediately

Separate the ideas into:

- **MVP**
- **Future features**
- **Unnecessary/overcomplicated features**

Don't write code yet. I want to establish a clear scope first.

### Prompt 2: Calculator Feature Ideas

Based on the project we're planning, brainstorm useful calculator features we could add.

Consider categories such as:

- Basic calculations
- Scientific calculations
- Graphing
- Unit conversion
- Currency conversion
- Finance
- Health
- Math/education
- Everyday calculations

For each idea, briefly explain:

- What it does
- Who would use it
- How difficult it would be to build
- Whether it would actually add value to the project

Don't rank them yet. I want to understand the options first.

### Prompt 3: Good/Bad Calculator Criteria

Based on the good/bad calculator examples I pasted, create a practical checklist of things we should consider before building or adding a calculator.

Focus on:

- Usability
- Accuracy
- UI/UX
- Mobile responsiveness
- Performance
- Accessibility
- Edge cases
- Whether the calculator actually solves a useful problem

Separate the checklist into:

- **Must-have**
- **Nice-to-have**
- **Avoid**

### Prompt 4: Getting Ready for Vercel

I asked another AI what makes a calculator good or bad, and I'm now thinking about deploying this project to Vercel.

Based on the current state of the project, review whether it seems ready for deployment.

Check for:

- Obvious bugs
- Broken features
- Responsive/mobile issues
- Build/deployment problems
- Missing environment variables or configuration
- Anything that could cause problems on Vercel

Tell me:

1. What is already ready
2. What should be fixed first
3. What can safely be improved after deployment

Don't make changes yet. Just review it.

### Prompt 5: Railway / Backend Ideas

I'm thinking about adding some more advanced features to the calculator and I'm wondering whether we actually need a backend.

Could we use something like Railway, or would that add unnecessary complexity?

First, look at the current architecture and explain:

1. What we can already do entirely on the frontend/Vercel
2. What features would actually benefit from a backend
3. Whether Railway would make sense for this project
4. Any simpler alternatives

Don't add a backend yet. I want to understand the trade-offs first.

### Prompt 6: Vercel vs Railway

Given the current project and the features we're planning, do we actually need Railway, or can we keep everything on Vercel?

Compare the two specifically for this project.

Consider:

- Current architecture
- Calculator functionality
- Future features
- Cost and complexity
- Deployment
- Scalability
- Whether we'd need a database
- Whether we'd need server-side logic

Give me a recommendation based on the actual project rather than generic pros and cons.

Before recommending anything, inspect the project structure and current implementation so your answer is based on what we're actually building.

### Prompt 7: Vercel 404

I'm getting a 404/not-found page on Vercel after deploying the project.

Please inspect the project structure and deployment configuration and figure out why Vercel isn't serving the app.

Check:

- Framework detection
- Build command
- Output directory
- Routing
- `package.json`
- Vercel configuration
- Whether the correct project/root directory is being deployed

Explain the root cause in simple terms first, then make the minimum changes needed to fix it.

After fixing it, tell me exactly what I should redeploy.

### Prompt 8: Scientific Calculator Improvements

I think the calculator can be improved, especially the scientific calculator.

Please review the current calculator from a user/product perspective and identify what feels incomplete, confusing, or missing.

For the scientific calculator specifically, check:

- Available functions
- Buttons and layout
- Trigonometric functions
- Logarithms
- Powers and roots
- Constants
- Degree/radian modes
- Parentheses and expression handling
- Error handling
- Mobile usability

Give me a prioritized list of improvements before changing any code.

### Prompt 9: Graphing Calculator

I want to add a graphing calculator to the project, inspired by the functionality people expect from tools like Desmos and TI-84 calculators.

Before coding, inspect the existing calculator architecture and propose how we should integrate graphing without breaking the current calculators.

Start with a realistic MVP.

It should support:

- Plotting functions such as `y = sin(x)` and `y = x²`
- Multiple functions
- Zooming and panning
- X/Y axes and grid
- Adjustable viewing range
- Common mathematical functions
- Degrees/radians where relevant
- Responsive desktop and mobile UI

Explain what library or approach you recommend and why.

Don't try to reproduce all of Desmos at once.

After we agree on the architecture, we'll build it incrementally.

### Prompt 10: Code Review + Fixes

**Code Review**

Please perform a thorough code review of the current project.

Look for:

- Bugs
- Incorrect calculations
- Logic errors
- Duplicated code
- Unnecessary complexity
- Performance issues
- Security issues
- Accessibility problems
- Mobile/responsive issues
- Poor component structure
- Potential edge cases
- Problems that could appear during production deployment

For each issue, explain:

1. What the problem is
2. Why it matters
3. How serious it is
4. How you would fix it

Don't change the code yet. Give me the review first.

### Prompt 11: Check the Sin/Cos Graph Calculations

I think our `sin(x)` and `cos(x)` graphs might be incorrect compared with the expected behavior.

Please independently verify the graphing calculations rather than assuming the current implementation is correct.

Test:

- `sin(x)`
- `cos(x)`
- Different x-axis ranges
- Degree vs radian behavior
- Important known points such as `0`, `π/2`, `π`, and `3π/2`
- Graph scaling and coordinate conversion
- Any transformations applied before rendering

Compare the calculated values with the expected mathematical values.

If something is wrong, identify the exact cause and explain it in simple terms.

Then fix the calculation/rendering issue and add or update tests so this problem doesn't come back.

### What did the AI do well?

It was good at discussing the problem with me before building: what makes a calculator good or bad, how big the scope should be, and whether to use Vercel or Railway. It explained problems clearly, such as why the Vercel link showed a 404. It also checked its own work by running the tests and taking screenshots of the app.

### What did the AI do poorly?

- It didn't notice that the graph used degrees, which made `sin(x)` look flat. I found that myself by comparing with Desmos.
- The code review I drafted with a different AI chat still had some of that AI's chat text in it, and two technical points were inaccurate.
- It started fixing code before we had agreed on a plan.

### For the places it did poorly, how did you change your approach to your prompts to improve its output?

I told it to discuss before building, and made decisions myself before letting it write code.

I used screenshots as references instead of describing things: my phone's calculator for the scientific keys, Desmos for the graph, and Vercel for the deploy error.

I asked it to self-check (for example, "self-check the calculation problem") and to explain how it tested things.

I used one AI to review another AI's work. Claude checked the code review and found the leftover text and the inaccurate points.