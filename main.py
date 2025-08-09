from dotenv import load_dotenv
load_dotenv()

import os
import asyncio
from threading import Thread
import uuid

import ngrok
from flask import Flask, request, Response

from agentmail import AgentMail
from agentmail_toolkit.openai import AgentMailToolkit
from agents import WebSearchTool, Agent, Runner

port = 8080
domain = os.getenv("WEBHOOK_DOMAIN")
username = os.getenv("INBOX_USERNAME")
inbox = f"{username}@agentmail.to"

if not username:
    print("⚠️  WARNING: INBOX_USERNAME is not set!")
    print("   Make sure your .env file contains: INBOX_USERNAME=hiring-test")

client_id = "hiring-agent-2"

listener = ngrok.forward(port, domain=domain, authtoken_from_env=True)
app = Flask(__name__)

client = AgentMail(api_key=os.getenv("AGENTMAIL_API_KEY"))

try:
    if os.getenv("INBOX_USERNAME"):
        print(f"Creating inbox: {username}")
        client.inboxes.create(username=username, client_id=client_id) 
    else:
        print("⚠️  WARNING: INBOX_USERNAME is not set!")
except Exception as exc:
    print(f"Inbox ensure skipped: {exc}")

# Get the ngrok public URL and display it
try:
    public_url = listener.url() if hasattr(listener, 'url') else None
    if not public_url:
        public_url = getattr(listener, 'public_url', None)
    if not public_url and domain:
        public_url = f"https://{domain}"
    
    if public_url:
        print(f"\n🔗 NGROK PUBLIC URL: {public_url}")
        print(f"📧 WEBHOOK URL SHOULD BE: {public_url}/webhooks")
        print(f"🎯 TEST URL: {public_url}/")
        
        # Try to auto-configure the webhook
        webhook_url = f"{public_url}/webhooks"
       
    else:
        print("⚠️  Could not determine ngrok public URL")
except Exception as e:
    print(f"Error getting ngrok URL: {e}")

# Load system prompt from file
try:
    with open("system_prompt.txt", "r") as f:
        system_prompt = f.read().strip()
    instructions = system_prompt.replace("{inbox}", inbox)
    print("✅ System prompt loaded from system_prompt.txt")
except FileNotFoundError:
    print("⚠️  WARNING: system_prompt.txt not found!")


agent = Agent(
    name="Hiring Agent",
    instructions=instructions,
    tools=AgentMailToolkit(client).get_tools() + [WebSearchTool()],
)

messages = []

@app.route("/", methods=["POST"])
def receive_webhook_root():
    print("WEBHOOK received at ROOT /")
    print(request.json)
    Thread(target=process_webhook, args=(request.json,)).start()
    return Response(status=200)

@app.route("/", methods=["GET"])
def root_get():
    return Response("Hiring Agent Webhook Endpoint", status=200)


def process_webhook(payload):
    global messages

    email = payload["message"]

    # Include attachment info if present
    attachments_info = ""
    if email.get("attachments"):
        attachments_info = "\nAttachments:\n"
        for att in email["attachments"]:
            attachments_info += f"- {att['filename']} (ID: {att['attachment_id']}, Type: {att['content_type']}, Size: {att['size']} bytes)\n"
    
    prompt = f"""
From: {email["from"]}
Subject: {email["subject"]}
Body:\n{email["text"]}
{attachments_info}

IMPORTANT FOR TOOL CALLS:
- THREAD_ID: {email.get("thread_id", "N/A")}
- MESSAGE_ID: {email.get("message_id", "N/A")}

Use these EXACT values when calling get_thread and get_attachment tools.
"""
    print("Prompt:\n\n", prompt, "\n")

    response = asyncio.run(Runner.run(agent, messages + [{"role": "user", "content": prompt}]))
    print("Response:\n\n", response.final_output, "\n")

    client.inboxes.messages.reply(
        inbox_id=inbox,
        message_id=email["message_id"],
        html=response.final_output,
    )

    messages = response.to_input_list()


if __name__ == "__main__":
    print(f"Inbox: {inbox}\n")

    app.run(port=port)
