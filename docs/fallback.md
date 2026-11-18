# When a call can't be answered normally

A caller forwarded from a groomer's own number must never meet silence or a dead line, or that customer is lost. There are three cases.

## 1. Over the business's daily call limit: take a message (automatic)

Once a business reaches its daily phone limit (`phone_daily_limit`, or `PHONE_DAILY_CALL_LIMIT`), further calls are still answered, by a short message-taking receptionist:

- It says it can't book right now and offers to take a message.
- It asks for the caller's name (spelled), the best number to call back, and what they need, reads them back, and saves a callback request.
- The owner sees it in the dashboard and gets an [email](email.md).
- It can't check times, quote prices or book. The backend refuses those tools on this call even if the model tries. Calls end after 3 minutes at most.

This works for both Vapi and OpenAI calls. Other refusals still decline the call: phone calls switched off, a caller over their hourly limit (abuse), or the deployment-wide ceiling (`PHONE_DAILY_TOTAL_LIMIT`, cost protection). For those, Vapi says "Sorry, our assistant can't take calls right now", and OpenAI calls get busy.

## 2. The backend is down: Vapi's fallback destination (set once per number)

If Vapi can't reach the backend for an assistant, it uses the phone number's **fallback destination**. Without one, the caller hears an error and the call ends.

In the Vapi dashboard, open **Phone Numbers →** the number **→ Fallback Destination** and set a phone number to transfer to.

**Don't use the number that forwards to the receptionist.** If the groomer forwards their shop line to this number when they don't answer, transferring back to the shop line forwards the caller back here, in a loop. Use a different phone, such as the owner's mobile when the shop line forwards. If there's no such phone, leave it empty and rely on step 3.

## 3. Vapi or the trunk is down: Twilio's voicemail (set once per number)

If the call doesn't reach Vapi (or the OpenAI trunk) at all, Twilio can play a message and take a voicemail.

1. In Twilio, **TwiML Bins → Create**, named `receptionist-voicemail`:

   ```xml
   <Response>
     <Say voice="Polly.Joanna">Sorry, our assistant can't answer right now. Please leave your name, your number, and what you need after the tone, and we'll call you back.</Say>
     <Record maxLength="120" playBeep="true" />
     <Say voice="Polly.Joanna">Thank you. Goodbye.</Say>
   </Response>
   ```

2. Open the number under **Phone Numbers → Active numbers**, and in its voice configuration set **Primary handler fails** to that TwiML Bin.
   For a number on an Elastic SIP Trunk, set the trunk's **Disaster Recovery URL** to the bin instead.

Voicemails appear in Twilio under **Monitor → Logs → Call recordings**. They aren't emailed automatically, so check them after any outage.

## Check it

1. Daily limit: on a test business, `phone_daily_limit` 1 in its file (load with `--update`), call twice. The second call offers to take a message. Leave one and check that the owner email arrives. Set the limit back.
2. Fallback: in Vapi, temporarily set the number's server URL to a wrong address and call. You should be transferred to the fallback destination. Put the URL back.
