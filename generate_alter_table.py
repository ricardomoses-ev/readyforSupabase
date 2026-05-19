import pandas as pd
import re

input_path = "output/close_leads_supabase_ready.csv"
df = pd.read_csv(input_path, low_memory=False, nrows=500)

missing_columns = [
    "display_name", "primary_contact_first_name", "primary_contact_last_name",
    "primary_contact_title", "primary_contact_primary_phone_type",
    "primary_contact_other_phones", "primary_contact_primary_email_type",
    "primary_contact_other_emails", "primary_contact_primary_url",
    "primary_contact_other_urls", "accountant", "accounting_firm",
    "analyst_or_account_manager_id", "analyst_or_account_manager_name",
    "campaign_old", "card_rev", "cfa_id", "cfa_name",
    "company_website_lead_crm", "date", "disco_date", "dupe_test",
    "email_status", "fb_or_fbx", "fbx_principal_id", "fbx_principal_name",
    "first_partner_deal_date", "first_partner_lead_sent_date",
    "further_lead_info", "gclid", "if_contract_end_date",
    "in_funnel_hot_or_warm", "last_partner_deal_date",
    "last_partner_lead_sent_date", "lc_deb_end_date", "lc_deb_start_date",
    "lead_owner_id", "lead_owner_name", "mbali_measure", "mob",
    "originator_id", "originator_name", "partner_agreement_signed_date",
    "partner_introducer", "partner_owner_id", "partner_owner_name",
    "partner_split_pct", "partner_split_type", "partner_type",
    "partner_prospect_or_lender", "quotezone_gt_6m", "r_and_d_hb",
    "sdlt_hb", "sent_to_partner", "smart_view_tag", "timestamp",
    "triage_assist", "webpage_or_form", "created_by", "created_by_name",
    "updated_by", "updated_by_name",
    "address_1_address_1", "address_1_address_2", "address_1_city",
    "address_1_state", "address_1_zip", "address_1_country",
    "address_2_address_1", "address_2_address_2", "address_2_city",
    "address_2_state", "address_2_zip", "address_2_country",
    "address_3_address_1", "address_3_address_2", "address_3_city",
    "address_3_state", "address_3_zip", "address_3_country",
    "address_4_address_1", "address_4_address_2", "address_4_city",
    "address_4_state", "address_4_zip", "address_4_country",
    "address_5_address_1", "address_5_address_2", "address_5_city",
    "address_5_state", "address_5_zip", "address_5_country",
    "active_opportunity_value_summary",
    "avg_annual_active_opportunity_value", "avg_annual_lost_opportunity_value",
    "avg_annual_opportunity_value", "avg_annual_won_opportunity_value",
    "avg_annualized_active_opportunity_value",
    "avg_annualized_lost_opportunity_value",
    "avg_annualized_opportunity_value",
    "avg_annualized_won_opportunity_value",
    "avg_monthly_active_opportunity_value",
    "avg_monthly_lost_opportunity_value", "avg_monthly_opportunity_value",
    "avg_monthly_won_opportunity_value",
    "avg_one_time_active_opportunity_value",
    "avg_one_time_lost_opportunity_value", "avg_one_time_opportunity_value",
    "avg_one_time_won_opportunity_value",
    "email_last_opened", "first_call_created", "first_call_disposition",
    "first_call_note", "first_call_outcome_id", "first_call_user",
    "first_communication_date", "first_communication_summary",
    "first_communication_type", "first_communication_user_id",
    "first_communication_user_name", "first_completed_meeting_outcome_id",
    "first_email", "first_email_attachments", "first_email_bcc",
    "first_email_cc", "first_email_created", "first_email_from",
    "first_email_opens", "first_email_template", "first_email_to",
    "first_email_user", "first_emailed", "first_emailed_template",
    "first_incoming_call_date", "first_incoming_email_date",
    "first_incoming_sms_date", "first_note_by", "first_note_user",
    "first_opportunity_status_change_new_status",
    "first_opportunity_status_change_old_status",
    "first_outgoing_call_date", "first_outgoing_email_date",
    "first_outgoing_sms_date", "first_received_sms_date",
    "first_sent_sms_date", "first_sms_created", "first_sms_date",
    "first_sms_text", "first_sms_user", "first_source",
    "first_voicemail_duration",
    "last_activity_date", "last_activity_type", "last_activity_user_id",
    "last_activity_user_name", "last_call_created", "last_call_disposition",
    "last_call_duration", "last_call_note", "last_call_outcome_id",
    "last_call_user_id", "last_call_user_name", "last_communication_date",
    "last_communication_summary", "last_communication_type",
    "last_communication_user_id", "last_communication_user_name",
    "last_complete_task_due_date", "last_complete_task_updated",
    "last_completed_meeting_outcome_id", "last_email_attachments",
    "last_email_bcc", "last_email_cc", "last_email_date", "last_email_from",
    "last_email_subject", "last_email_to", "last_email_user",
    "last_incoming_call_date", "last_incoming_email_date",
    "last_incoming_sms_date", "last_lead_status_change_date",
    "last_note_by", "last_note_created", "last_note_user",
    "last_opportunity_status_change_date", "last_outgoing_call_date",
    "last_outgoing_email_date", "last_outgoing_sms_date",
    "last_received_sms_date", "last_sent_sms_date", "last_sms_created",
    "last_sms_date", "last_sms_text", "last_sms_user", "last_task_creator",
    "last_task_due", "last_voicemail_duration",
    "lost_opportunity_value_summary",
    "max_annual_active_opportunity_value", "max_annual_lost_opportunity_value",
    "max_annual_opportunity_value", "max_annual_won_opportunity_value",
    "max_annualized_active_opportunity_value",
    "max_annualized_lost_opportunity_value",
    "max_annualized_opportunity_value",
    "max_annualized_won_opportunity_value",
    "max_monthly_active_opportunity_value",
    "max_monthly_lost_opportunity_value", "max_monthly_opportunity_value",
    "max_monthly_won_opportunity_value",
    "max_one_time_active_opportunity_value",
    "max_one_time_lost_opportunity_value", "max_one_time_opportunity_value",
    "max_one_time_won_opportunity_value",
    "min_annual_active_opportunity_value", "min_annual_lost_opportunity_value",
    "min_annual_opportunity_value", "min_annual_won_opportunity_value",
    "min_annualized_active_opportunity_value",
    "min_annualized_lost_opportunity_value",
    "min_annualized_opportunity_value",
    "min_annualized_won_opportunity_value",
    "min_monthly_active_opportunity_value",
    "min_monthly_lost_opportunity_value", "min_monthly_opportunity_value",
    "min_monthly_won_opportunity_value",
    "min_one_time_active_opportunity_value",
    "min_one_time_lost_opportunity_value", "min_one_time_opportunity_value",
    "min_one_time_won_opportunity_value",
    "next_task_date", "next_task_due_date", "next_task_text",
    "next_task_user_id", "next_task_user_name",
    "num_active_opportunities", "num_activities", "num_addresses",
    "num_annual_opportunities", "num_calls", "num_canceled_meetings",
    "num_completed_meetings", "num_completed_tasks", "num_contact_urls",
    "num_contacts", "num_declined_by_lead_meetings",
    "num_declined_by_org_meetings", "num_declined_meetings",
    "num_email_addresses", "num_email_attachments", "num_emails",
    "num_in_progress_meetings", "num_incoming_calls", "num_incomplete_tasks",
    "num_lost_opportunities", "num_meetings", "num_missed_calls",
    "num_monthly_opportunities", "num_notes", "num_one_time_opportunities",
    "num_opportunities", "num_outgoing_calls", "num_outgoing_emails",
    "num_phone_numbers", "num_received_emails", "num_received_sms",
    "num_sent_emails", "num_sent_sms", "num_sms", "num_tasks",
    "num_upcoming_meetings", "num_urls", "num_won_opportunities",
    "primary_opportunity_confidence", "primary_opportunity_created",
    "primary_opportunity_date_won", "primary_opportunity_period",
    "primary_opportunity_pipeline_id", "primary_opportunity_pipeline_name",
    "primary_opportunity_status", "primary_opportunity_status_label",
    "primary_opportunity_status_type", "primary_opportunity_updated",
    "primary_opportunity_user_id", "primary_opportunity_user_name",
    "primary_opportunity_value", "primary_opportunity_value_summary",
    "times_communicated",
    "total_annual_active_opportunity_value",
    "total_annual_lost_opportunity_value", "total_annual_opportunity_value",
    "total_annual_won_opportunity_value",
    "total_annualized_active_opportunity_value",
    "total_annualized_lost_opportunity_value",
    "total_annualized_opportunity_value",
    "total_annualized_won_opportunity_value",
    "total_monthly_active_opportunity_value",
    "total_monthly_lost_opportunity_value", "total_monthly_opportunity_value",
    "total_monthly_won_opportunity_value",
    "total_one_time_active_opportunity_value",
    "total_one_time_lost_opportunity_value",
    "total_one_time_opportunity_value",
    "total_one_time_won_opportunity_value",
    "total_opportunity_value_summary", "won_opportunity_value_summary",
]


def infer_pg_type(series, col_name):
    non_null = series.dropna()
    if len(non_null) == 0:
        return "text"

    if col_name.startswith("num_") or col_name == "times_communicated":
        return "integer"

    if "duration" in col_name and "summary" not in col_name:
        return "integer"

    if re.search(r"_value$", col_name) and "summary" not in col_name:
        return "numeric"

    if col_name == "primary_opportunity_confidence":
        return "integer"

    if col_name == "first_email_opens":
        return "integer"

    date_patterns = [
        "_date$", "_created$", "_updated$", "^date$", "^timestamp$",
        "_opened$", "_emailed$",
    ]
    for pat in date_patterns:
        if re.search(pat, col_name):
            return "text"

    return "text"


lines = ["-- Add missing columns to leads table for Close CRM import", ""]

for col in missing_columns:
    if col in df.columns:
        pg_type = infer_pg_type(df[col], col)
    else:
        pg_type = "text"
    lines.append(f'ALTER TABLE public.leads ADD COLUMN IF NOT EXISTS "{col}" {pg_type} NULL;')

sql = "\n".join(lines)

output_sql = "output/alter_leads_add_columns.sql"
with open(output_sql, "w") as f:
    f.write(sql)

print(f"Generated {len(missing_columns)} ALTER TABLE statements")
print(f"Saved to: {output_sql}")
