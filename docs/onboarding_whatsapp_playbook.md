# Onboarding WhatsApp playbook

Single source of truth for lead/client WhatsApp drafts.
Edit bodies here; the app loads stages at runtime and **selects** a suggested stage from lead state.
Advisors can still edit the preview before Copy / Open WhatsApp / API send.

Placeholders: `{first_name}`, `{sender}`, `{quiz_url}`, `{note_snippet}`, `{referral}`, `{meeting_hint}`, `{lead_name}`

---

## first_contact

**Label:** First contact

```
Hi {first_name},

Thank you for connecting with {sender}. We are a SEBI Registered Investment Adviser and help families in building a comprehensive investment plan and focused equity advisory for wealth creation.

We have more than 12 years of experience running client-oriented, fee-based investment advisory (no product commissions).

A few links that may help:
• Website: https://www.equities4wealth.com/
• Anshul’s LinkedIn: https://www.linkedin.com/in/anshul-khare-5567b61a
• SEBI Investment Adviser list (you can search for “Anshul Khare” or registration INA000018090): https://www.sebi.gov.in/sebiweb/other/OtherAction.do?doRecognisedFpi=yes&intmId=13

Would you be open to a short call this week so we can understand your situation better?

{note_snippet}

Regards,
{sender}
```

---

## followup_1

**Label:** Follow-up 1 (gentle)

```
Hi {first_name},

Hope you are doing well. Just following up on our earlier note about investment planning with {sender}.

Would you have 10–15 minutes this week for a quick call?

{note_snippet}

Regards,
{sender}
```

---

## followup_2

**Label:** Follow-up 2 (direct)

```
Hi {first_name},

Checking in once more — happy to answer any questions about how we work and whether we are a fit for {lead_name}'s goals.

If now is not the right time, just let me know and I will close the loop.

{note_snippet}

Regards,
{sender}
```

---

## meeting_nudge

**Label:** Meeting nudge

```
Hi {first_name},

Looking forward to connecting{meeting_hint}.

Please share a couple of convenient slots if you have not already, or reply here and I will send a calendar invite.

{note_snippet}

Regards,
{sender}
```

---

## risk_invite

**Label:** Risk profile invite

```
Hi {first_name},

Thank you for connecting with {sender}. Please complete this short risk assessment so we can tailor your investment plan:

{quiz_url}

It takes about 5 minutes. Happy to answer any questions.

Regards,
{sender}
```

---

## proposal_sent_nudge

**Label:** Proposal follow-up

```
Hi {first_name},

Hope you had a chance to review the proposal we shared. Happy to walk through any questions on fees, horizon, or the recommended approach.

{note_snippet}

Regards,
{sender}
```

---

## annual_risk_refresh

**Label:** Annual risk refresh (clients)

```
Hi {first_name},

As part of our annual review, please refresh your risk profile so your portfolio stays aligned with your situation:

{quiz_url}

It only takes a few minutes. Reply here if you have any questions.

Regards,
{sender}
```
