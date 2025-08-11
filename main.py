from dotenv import load_dotenv
load_dotenv()

import os
import asyncio
from threading import Thread
import uuid

from flask import Flask, request, Response

from agentmail import AgentMail
from agentmail_toolkit.openai import AgentMailToolkit
from agents import WebSearchTool, Agent, Runner

port = int(os.getenv("PORT", 8080))
username = os.getenv("INBOX_USERNAME")
inbox = f"{username}@agentmail.to"

if not username:
    print("⚠️  WARNING: INBOX_USERNAME is not set!")
    print("   Make sure your .env file contains: INBOX_USERNAME=hiring-test")

client_id = "hiring-agent-1"

app = Flask(__name__)

client = AgentMail(api_key=os.getenv("AGENTMAIL_API_KEY"))

inbox_obj = client.inboxes.create(username=username, client_id=client_id) 
inbox_address = f"{username}@agentmail.to"

webhook_url = os.getenv("WEBHOOK_URL")

client.webhooks.create(
    url=webhook_url,
    inbox_ids=[inbox_obj.inbox_id],
    event_types=["message.received"],
    client_id="hiring-agent-webhook",
)

system_prompt = os.getenv("SYSTEM_PROMPT")
if system_prompt:
    instructions = system_prompt.strip().replace("{inbox}", inbox_address)
    print("System prompt loaded from environment variable")
else:
    print("WARNING: SYSTEM_PROMPT environment variable not set!")
    # Fallback to a basic prompt
    instructions = f"You are a hiring agent for the inbox {inbox_address}. Help candidates with their applications."


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
        inbox_id=inbox_obj.inbox_id,
        message_id=email["message_id"],
        html=response.final_output,
    )

    messages = response.to_input_list()


if __name__ == "__main__":
    print(f"Inbox: {inbox_address}\n")
    print(f"Starting server on port {port}")

    app.run(host="0.0.0.0", port=port)
