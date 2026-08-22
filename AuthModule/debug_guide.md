# Local Debugging & Testing Guide: SSO Workflow

To see the entire SSO workflow in action and debug it locally on your Windows machine, you need to run two things: the **Aegis Security Gateway** and your new **Client API**. 

Here is the exact step-by-step setup to test the full pipeline.

---

### Step 1: Start the Aegis Security Gateway (Backend)
The Aegis Gateway is the main backend that actually performs the AI guardrails. 

1. Open a new PowerShell terminal.
2. Navigate to the root folder: `cd c:\Ramendra\AI-Learning\AuthenitcationModule\AuthenticationSDKAI`
3. Run the gateway locally. If you have Docker, run:
   ```powershell
   docker-compose up --build
   ```
   *If you do not have Docker running, you can run the server natively:*
   ```powershell
   pip install -r requirements.txt
   uvicorn aegis_ai.server:app --host 0.0.0.0 --port 8080 --reload
   ```

---

### Step 2: Start the Client API (Agent A)
The Client API is the FastAPI application we just built. It runs on a different port (`8000`) so it doesn't conflict with Aegis (`8080`).

1. Open a **second** PowerShell terminal.
2. Navigate to the client folder:
   ```powershell
   cd c:\Ramendra\AI-Learning\AuthenitcationModule\AuthenticationSDKAI\client_app
   ```
3. Install dependencies and start the app with `--reload` (so you can add breakpoints and edit code live):
   ```powershell
   pip install -r requirements.txt
   uvicorn main:app --host 0.0.0.0 --port 8000 --reload
   ```

---

### Step 3: Test the SSO Workflow
Now that both servers are running, you can act as the "End User" logging in.

#### Option A: Interactive Debugging (Swagger UI)
This is the easiest way to test and debug:
1. Open your web browser and go to: `http://localhost:8000/docs`
2. You will see the `/api/secure/analyze` endpoint.
3. Click **"Try it out"**.
4. For the `authorization` header, type: `Bearer my-fake-sso-token` *(In this demo, our FastAPI client just checks that a token exists)*.
5. Enter a prompt like `"Analyze this data for security risks. My email is admin@company.com."`
6. Click **Execute**.

**What to watch for:**
- Look at the terminal running your **Client API**. You will see it successfully generate the M2M token and call Aegis.
- Look at the terminal running your **Aegis Gateway**. You will see it intercept the request, perform AuthN/AuthZ, and print out logs indicating it masked the email address `admin@company.com` before sending it to the LLM!

#### Option B: Terminal Debugging (cURL/PowerShell)
You can trigger the workflow directly from a third terminal using PowerShell:
```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/secure/analyze?prompt=Analyze%20this%20data%20for%20security%20risks.%20My%20email%20is%20admin@company.com" -Method POST -Headers @{ "Authorization" = "Bearer employee-sso-token-123" }
```

### How to add Breakpoints
If you want to freeze the code mid-execution to see the SSO tokens being generated:
1. Open `client_app/main.py` in your IDE.
2. Find the `secure_analyze` function (around line 52).
3. Add `import pdb; pdb.set_trace()` right before the `fetch_sso_token_for_agent_a()` call.
4. When you trigger the API via the Swagger UI, your terminal will freeze. You can then type `p agent_a_sso_token` to literally inspect the SSO JWT token before it gets sent to Aegis!
