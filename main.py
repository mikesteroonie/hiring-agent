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
    email = payload["message"]
    thread_id = email.get("thread_id")
    
    if not thread_id:
        print("⚠️  No thread_id found in email payload")
        return
    
    try:
        thread = client.inboxes.threads.get(inbox_id=inbox_obj.inbox_id, thread_id=thread_id)
        print(f"🔍 DEBUG: Fetched thread {thread_id} with {len(thread.messages)} messages")
        
        # Detect if the job/role details block has already been sent in this thread
        has_already_sent_job_block = False
        for _m in thread.messages:
            _content = (_m.text or _m.html or "")
            if (
                "For legal reasons I am copy pasting the details of the role" in _content
                or "<strong>Role:</strong> Founding Engineer" in _content
            ):
                has_already_sent_job_block = True
                break
        
        thread_context = []
        for msg in thread.messages:
            # In AgentMail, messages from external senders are "user" messages
            # Messages sent from your inbox are "assistant" messages
            message_content = msg.text or msg.html or "No content"
            
            # Check if this message is from an external sender (not from your inbox)
            if hasattr(msg, 'from_') and msg.from_ and not msg.from_.endswith('@agentmail.to'):
                thread_context.append({"role": "user", "content": message_content})
            else:
                # This is a message from your inbox (assistant)
                thread_context.append({"role": "assistant", "content": message_content})
        
        print(f"🔍 DEBUG: Thread context has {len(thread_context)} messages")
        
    except Exception as e:
        print(f"⚠️  Error fetching thread {thread_id}: {e}")
        thread_context = []
        has_already_sent_job_block = False

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

    # Guardrail: Only include the role/company/benefits block once per thread
    if has_already_sent_job_block:
        prompt += (
            "\nThe role/company/benefits block has ALREADY been sent earlier in this thread. "
            "Do NOT include or repeat that block again. Respond with ONLY a concise follow-up question "
            "based on the candidate's resume and prior messages."
        )
    else:
        prompt += (
            "\nThis is the FIRST reply in this thread that includes the role/company/benefits block. "
            "Include that block exactly once as specified by your instructions, then ask ONE concise question."
        )
    
    print("Prompt:\n\n", prompt, "\n")

    # Pass the actual thread context to the agent
    response = asyncio.run(Runner.run(agent, thread_context + [{"role": "user", "content": prompt}]))
    print("Response:\n\n", response.final_output, "\n")

    client.inboxes.messages.reply(
        inbox_id=inbox_obj.inbox_id,
        message_id=email["message_id"],
        html=response.final_output,
    )


if __name__ == "__main__":
    print(f"Inbox: {inbox_address}\n")
    print(f"Starting server on port {port}")

    app.run(host="0.0.0.0", port=port)
