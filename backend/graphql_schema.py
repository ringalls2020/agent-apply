from __future__ import annotations

import os
from time import sleep
from uuid import NAMESPACE_URL, uuid5

import graphene
from fastapi import HTTPException, Request
from graphql import GraphQLError

from .auth import authenticated_user_id_from_request, create_user_access_token
from .cloud_client import CloudClientError
from .models import (
    ApplicationProfileUpsertRequest,
    ApplicationRecord,
    ApplicationsSearchResponse,
    ApplicationStatus,
    ApplyRunStartRequest,
    ApplyTargetJob,
    BulkApplyResponse,
    BulkApplySkippedItem,
    MatchRunStartRequest,
    MatchRunStatus,
    MatchRunStatusResponse,
    Opportunity,
    PreferenceUpsertRequest,
    ResumeUpsertRequest,
    UserUpsertRequest,
)
from common.time import utc_now


def _derive_application_source(job_url: str) -> str:
    try:
        host = job_url.split("//", 1)[-1].split("/", 1)[0].lower()
    except Exception:
        return "other"
    if "greenhouse" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "smartrecruiters" in host:
        return "smartrecruiters"
    if "myworkdayjobs.com" in host or "workday" in host:
        return "workday"
    return "other"


class ContactType(graphene.ObjectType):
    name = graphene.String(required=True)
    email = graphene.String(required=True)
    role = graphene.String()
    source = graphene.String(required=True)


class OpportunityType(graphene.ObjectType):
    id = graphene.ID(required=True)
    title = graphene.String(required=True)
    company = graphene.String(required=True)
    url = graphene.String(required=True)
    reason = graphene.String(required=True)
    discovered_at = graphene.DateTime(required=True)


class ApplicationRecordType(graphene.ObjectType):
    id = graphene.ID(required=True)
    opportunity = graphene.Field(OpportunityType, required=True)
    status = graphene.String(required=True)
    is_archived = graphene.Boolean(required=True)
    contact = graphene.Field(ContactType)
    submitted_at = graphene.DateTime()
    notified_at = graphene.DateTime()
    title = graphene.String(required=True)
    company = graphene.String(required=True)
    source = graphene.String(required=True)
    contact_name = graphene.String(required=True)
    contact_email = graphene.String(required=True)
    job_url = graphene.String(required=True)

    def resolve_title(parent, _info):
        return parent["opportunity"]["title"]

    def resolve_company(parent, _info):
        return parent["opportunity"]["company"]

    def resolve_source(parent, _info):
        return _derive_application_source(parent["opportunity"]["url"])

    def resolve_contact_name(parent, _info):
        contact = parent.get("contact")
        return contact.get("name", "") if contact else ""

    def resolve_contact_email(parent, _info):
        contact = parent.get("contact")
        return contact.get("email", "") if contact else ""

    def resolve_job_url(parent, _info):
        return parent["opportunity"]["url"]


class AuthUserProfileType(graphene.ObjectType):
    id = graphene.ID(required=True)
    full_name = graphene.String(required=True)
    email = graphene.String(required=True)
    interests = graphene.List(graphene.NonNull(graphene.String), required=True)
    applications_per_day = graphene.Int(required=True)
    resume_filename = graphene.String()
    autosubmit_enabled = graphene.Boolean(required=True)


class AuthPayloadType(graphene.ObjectType):
    token = graphene.String(required=True)
    user = graphene.Field(AuthUserProfileType, required=True)


class ApplicationsSearchResultType(graphene.ObjectType):
    applications = graphene.List(graphene.NonNull(ApplicationRecordType), required=True)
    total_count = graphene.Int(required=True)
    limit = graphene.Int(required=True)
    offset = graphene.Int(required=True)


class BulkApplySkippedItemType(graphene.ObjectType):
    application_id = graphene.ID(required=True)
    reason = graphene.String(required=True)
    status = graphene.String()


class BulkApplyResponseType(graphene.ObjectType):
    run_id = graphene.String()
    status_url = graphene.String()
    accepted_application_ids = graphene.List(graphene.NonNull(graphene.ID), required=True)
    skipped = graphene.List(graphene.NonNull(BulkApplySkippedItemType), required=True)
    applications = graphene.List(graphene.NonNull(ApplicationRecordType), required=True)


class CustomAnswerOverrideType(graphene.ObjectType):
    question_key = graphene.String(required=True)
    answer = graphene.String(required=True)


class SensitiveProfileType(graphene.ObjectType):
    gender = graphene.String(required=True)
    race_ethnicity = graphene.String(required=True)
    veteran_status = graphene.String(required=True)
    disability_status = graphene.String(required=True)


class ApplicationProfileType(graphene.ObjectType):
    user_id = graphene.ID(required=True)
    autosubmit_enabled = graphene.Boolean(required=True)
    phone = graphene.String()
    city = graphene.String()
    state = graphene.String()
    country = graphene.String()
    linkedin_url = graphene.String()
    github_url = graphene.String()
    portfolio_url = graphene.String()
    work_authorization = graphene.String()
    requires_sponsorship = graphene.Boolean()
    willing_to_relocate = graphene.Boolean()
    years_experience = graphene.Int()
    writing_voice = graphene.String()
    cover_letter_style = graphene.String()
    achievements_summary = graphene.String()
    custom_answers = graphene.List(graphene.NonNull(CustomAnswerOverrideType), required=True)
    additional_context = graphene.String()
    sensitive = graphene.Field(SensitiveProfileType, required=True)


class UserType(graphene.ObjectType):
    id = graphene.ID(required=True)
    full_name = graphene.String(required=True)
    email = graphene.String(required=True)
    created_at = graphene.DateTime(required=True)
    updated_at = graphene.DateTime(required=True)


class PreferenceType(graphene.ObjectType):
    user_id = graphene.ID(required=True)
    interests = graphene.List(graphene.NonNull(graphene.String), required=True)
    locations = graphene.List(graphene.NonNull(graphene.String), required=True)
    seniority = graphene.String()
    applications_per_day = graphene.Int(required=True)
    created_at = graphene.DateTime(required=True)
    updated_at = graphene.DateTime(required=True)


class ResumeType(graphene.ObjectType):
    id = graphene.ID(required=True)
    user_id = graphene.ID(required=True)
    filename = graphene.String(required=True)
    resume_text = graphene.String(required=True)
    updated_at = graphene.DateTime(required=True)


class CustomAnswerOverrideInput(graphene.InputObjectType):
    question_key = graphene.String(required=True)
    answer = graphene.String(required=True)


class SensitiveProfileInput(graphene.InputObjectType):
    gender = graphene.String()
    race_ethnicity = graphene.String()
    veteran_status = graphene.String()
    disability_status = graphene.String()


class ApplicationProfileInput(graphene.InputObjectType):
    autosubmit_enabled = graphene.Boolean(required=True)
    phone = graphene.String()
    city = graphene.String()
    state = graphene.String()
    country = graphene.String()
    linkedin_url = graphene.String()
    github_url = graphene.String()
    portfolio_url = graphene.String()
    work_authorization = graphene.String()
    requires_sponsorship = graphene.Boolean()
    willing_to_relocate = graphene.Boolean()
    years_experience = graphene.Int()
    writing_voice = graphene.String()
    cover_letter_style = graphene.String()
    achievements_summary = graphene.String()
    custom_answers = graphene.List(graphene.NonNull(CustomAnswerOverrideInput))
    additional_context = graphene.String()
    sensitive = graphene.InputField(SensitiveProfileInput)


class ApplicationFilterInput(graphene.InputObjectType):
    statuses = graphene.List(graphene.NonNull(graphene.String))
    q = graphene.String()
    companies = graphene.List(graphene.NonNull(graphene.String))
    sources = graphene.List(graphene.NonNull(graphene.String))
    include_archived = graphene.Boolean(default_value=False)
    has_contact = graphene.Boolean()
    discovered_from = graphene.DateTime()
    discovered_to = graphene.DateTime()
    sort_by = graphene.String(default_value="discovered_at")
    sort_dir = graphene.String(default_value="desc")


def _to_dict(model_or_none):
    if model_or_none is None:
        return None
    return model_or_none.model_dump()


def _handle_exception(exc: Exception) -> None:
    if isinstance(exc, HTTPException):
        raise GraphQLError(exc.detail)
    if isinstance(exc, CloudClientError):
        raise GraphQLError(str(exc))
    if isinstance(exc, ValueError):
        raise GraphQLError(str(exc))
    raise exc


def _build_auth_user_profile(request: Request, user_id: str):
    app = request.app
    user = app.state.main_store.get_user(user_id)
    if user is None:
        raise GraphQLError("User not found for token subject")
    preferences = app.state.main_store.get_preferences(user_id)
    resume = app.state.main_store.get_resume(user_id)
    profile = app.state.main_store.get_application_profile(user_id)
    return {
        "id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "interests": preferences.interests if preferences else [],
        "applications_per_day": preferences.applications_per_day if preferences else 25,
        "resume_filename": resume.filename if resume else None,
        "autosubmit_enabled": profile.autosubmit_enabled if profile else False,
    }


def _run_agent(request: Request) -> list[dict]:
    app = request.app
    if not bool(getattr(app.state, "enable_dev_run_agent", False)):
        raise GraphQLError("runAgent is disabled in this environment")

    user_id = authenticated_user_id_from_request(request)
    preferences = app.state.main_store.get_preferences(user_id)
    if preferences is None or not preferences.interests:
        raise GraphQLError("User preferences not found")
    resume = app.state.main_store.get_resume(user_id)
    if resume is None or not resume.resume_text.strip():
        raise GraphQLError("User resume not found")

    profile = app.state.main_store.get_application_profile(user_id)
    autosubmit_enabled = profile.autosubmit_enabled if profile else False

    match_limit = min(max(preferences.applications_per_day, 1), 100)
    poll_interval_seconds = float(os.getenv("AGENT_RUN_MATCH_POLL_INTERVAL_SECONDS", "0.5"))
    poll_max_attempts = max(1, int(os.getenv("AGENT_RUN_MATCH_POLL_MAX_ATTEMPTS", "40")))

    if bool(getattr(app.state, "enable_run_agent_discovery_kick", False)):
        try:
            app.state.cloud_client.kick_discovery()
        except CloudClientError:
            pass

    started = app.state.orchestrator.start_match_run(
        user_id=user_id,
        payload=MatchRunStartRequest(
            limit=match_limit,
            location=preferences.locations[0] if preferences.locations else None,
            seniority=preferences.seniority,
        ),
    )

    latest_status: MatchRunStatusResponse | None = None
    for _ in range(poll_max_attempts):
        latest_status = app.state.orchestrator.get_match_run(user_id=user_id, run_id=started.run_id)
        if latest_status.status in {MatchRunStatus.completed, MatchRunStatus.partial, MatchRunStatus.failed}:
            break
        sleep(max(0.05, poll_interval_seconds))

    if latest_status is None or latest_status.status not in {MatchRunStatus.completed, MatchRunStatus.partial, MatchRunStatus.failed}:
        raise GraphQLError("Timed out waiting for match run completion")
    if latest_status.status == MatchRunStatus.failed:
        raise GraphQLError(latest_status.error or "Match run failed")

    now = utc_now()
    existing_by_opportunity_id = {
        record.opportunity.id: record
        for record in app.state.store.get_for_user_by_opportunity_ids(
            user_id=user_id,
            opportunity_ids=[match.external_job_id for match in latest_status.results],
            include_archived=True,
        )
    }
    applications: list[ApplicationRecord] = []
    for match in latest_status.results:
        existing_record = existing_by_opportunity_id.get(match.external_job_id)
        discovered_anchor = match.posted_at or (existing_record.opportunity.discovered_at if existing_record else now)
        record = ApplicationRecord(
            id=str(uuid5(NAMESPACE_URL, f"{user_id}:{match.external_job_id}")),
            opportunity=Opportunity(
                id=match.external_job_id,
                title=match.title,
                company=match.company,
                url=match.apply_url,
                reason=f"{match.reason} (source={match.source}, score={match.score:.2f})",
                discovered_at=discovered_anchor,
            ),
            status=ApplicationStatus.applying if autosubmit_enabled else ApplicationStatus.review,
        )
        applications.append(app.state.store.upsert_for_user(user_id, record))

    jobs_to_apply = [item for item in applications if item.status == ApplicationStatus.applying and not item.is_archived]
    if autosubmit_enabled and jobs_to_apply:
        app.state.orchestrator.start_apply_run(
            user_id=user_id,
            payload=ApplyRunStartRequest(
                jobs=[
                    ApplyTargetJob(
                        external_job_id=item.opportunity.id,
                        title=item.opportunity.title,
                        company=item.opportunity.company,
                        apply_url=item.opportunity.url,
                    )
                    for item in jobs_to_apply
                ]
            ),
        )

    return [_to_dict(item) for item in applications]


class Query(graphene.ObjectType):
    me = graphene.Field(AuthUserProfileType, required=True)
    applications = graphene.List(graphene.NonNull(ApplicationRecordType), include_archived=graphene.Boolean(default_value=False), required=True)
    applications_search = graphene.Field(ApplicationsSearchResultType, filter=graphene.Argument(ApplicationFilterInput), limit=graphene.Int(default_value=25), offset=graphene.Int(default_value=0), required=True)
    profile = graphene.Field(ApplicationProfileType, required=True)

    def resolve_me(self, info):
        request = info.context["request"]
        user_id = authenticated_user_id_from_request(request)
        return _build_auth_user_profile(request, user_id)

    def resolve_applications(self, info, include_archived=False):
        request = info.context["request"]
        user_id = authenticated_user_id_from_request(request)
        return [_to_dict(item) for item in request.app.state.store.list_for_user(user_id, include_archived=include_archived)]

    def resolve_applications_search(self, info, filter=None, limit=25, offset=0):
        request = info.context["request"]
        user_id = authenticated_user_id_from_request(request)
        parsed_statuses = []
        for raw in (filter.statuses if filter else []) or []:
            parsed_statuses.append(ApplicationStatus(raw.strip().lower()))
        applications, total_count = request.app.state.store.search_for_user(
            user_id=user_id,
            statuses=parsed_statuses,
            q=filter.q if filter else None,
            companies=(filter.companies if filter else None) or [],
            sources=(filter.sources if filter else None) or [],
            has_contact=filter.has_contact if filter else None,
            discovered_from=filter.discovered_from if filter else None,
            discovered_to=filter.discovered_to if filter else None,
            sort_by=(filter.sort_by if filter else "discovered_at") or "discovered_at",
            sort_dir=(filter.sort_dir if filter else "desc") or "desc",
            limit=limit,
            offset=offset,
            include_archived=(filter.include_archived if filter else False) or False,
        )
        resp = ApplicationsSearchResponse(
            applications=applications,
            total_count=total_count,
            limit=min(max(limit, 1), 100),
            offset=max(offset, 0),
        )
        return _to_dict(resp)

    def resolve_profile(self, info):
        request = info.context["request"]
        user_id = authenticated_user_id_from_request(request)
        profile = request.app.state.orchestrator.get_application_profile(user_id)
        if profile is None:
            raise GraphQLError("Profile not found")
        return _to_dict(profile)


class Signup(graphene.Mutation):
    class Arguments:
        full_name = graphene.String(required=True)
        email = graphene.String(required=True)
        password = graphene.String(required=True)

    Output = AuthPayloadType

    def mutate(self, info, full_name: str, email: str, password: str):
        request = info.context["request"]
        app = request.app
        existing_user = app.state.main_store.get_user_by_email(email)
        if existing_user is not None:
            raise GraphQLError("Account with this email already exists.")

        from uuid import uuid4
        from .security import hash_password

        user_id = str(uuid4())
        user = app.state.orchestrator.upsert_user(
            user_id=user_id,
            payload=UserUpsertRequest(full_name=full_name, email=email),
        )
        password_salt, password_hash = hash_password(password)
        app.state.main_store.set_user_password(
            user_id=user.id,
            password_salt=password_salt,
            password_hash=password_hash,
        )
        return {
            "token": create_user_access_token(request, user.id),
            "user": _build_auth_user_profile(request, user.id),
        }


class Login(graphene.Mutation):
    class Arguments:
        email = graphene.String(required=True)
        password = graphene.String(required=True)

    Output = AuthPayloadType

    def mutate(self, info, email: str, password: str):
        request = info.context["request"]
        user = request.app.state.main_store.verify_user_credentials(email=email, password=password)
        if user is None:
            raise GraphQLError("Invalid credentials.")
        return {
            "token": create_user_access_token(request, user.id),
            "user": _build_auth_user_profile(request, user.id),
        }


class Mutation(graphene.ObjectType):
    signup = Signup.Field()
    login = Login.Field()
    run_agent = graphene.List(graphene.NonNull(ApplicationRecordType), required=True)
    update_preferences = graphene.Field(PreferenceType, interests=graphene.List(graphene.NonNull(graphene.String), required=True), locations=graphene.List(graphene.NonNull(graphene.String)), seniority=graphene.String(), applications_per_day=graphene.Int(default_value=25), required=True)
    upload_resume = graphene.Field(
        ResumeType,
        filename=graphene.String(required=True),
        resume_text=graphene.String(),
        file_content_base64=graphene.String(),
        file_mime_type=graphene.String(),
        required=True,
    )
    apply_selected_applications = graphene.Field(BulkApplyResponseType, application_ids=graphene.List(graphene.NonNull(graphene.ID), required=True), required=True)
    mark_application_viewed = graphene.Field(ApplicationRecordType, application_id=graphene.ID(required=True), required=True)
    mark_application_applied = graphene.Field(ApplicationRecordType, application_id=graphene.ID(required=True), required=True)
    update_profile = graphene.Field(ApplicationProfileType, input=graphene.Argument(ApplicationProfileInput, required=True), required=True)

    def resolve_run_agent(self, info):
        return _run_agent(info.context["request"])

    def resolve_update_preferences(self, info, interests, locations=None, seniority=None, applications_per_day=25):
        request = info.context["request"]
        user_id = authenticated_user_id_from_request(request)
        try:
            result = request.app.state.orchestrator.upsert_preferences(
                user_id=user_id,
                payload=PreferenceUpsertRequest(
                    interests=interests,
                    locations=locations or [],
                    seniority=seniority,
                    applications_per_day=applications_per_day,
                ),
            )
            return _to_dict(result)
        except Exception as exc:
            _handle_exception(exc)

    def resolve_upload_resume(
        self,
        info,
        filename: str,
        resume_text: str | None = None,
        file_content_base64: str | None = None,
        file_mime_type: str | None = None,
    ):
        request = info.context["request"]
        user_id = authenticated_user_id_from_request(request)
        result = request.app.state.orchestrator.upsert_resume(
            user_id=user_id,
            payload=ResumeUpsertRequest(
                filename=filename,
                resume_text=resume_text,
                file_content_base64=file_content_base64,
                file_mime_type=file_mime_type,
            ),
        )
        return _to_dict(result)

    def resolve_apply_selected_applications(self, info, application_ids):
        request = info.context["request"]
        user_id = authenticated_user_id_from_request(request)
        normalized_ids: list[str] = []
        seen_ids: set[str] = set()
        for raw_id in application_ids:
            normalized_id = str(raw_id).strip()
            if not normalized_id or normalized_id in seen_ids:
                continue
            normalized_ids.append(normalized_id)
            seen_ids.add(normalized_id)

        if not normalized_ids:
            return _to_dict(
                BulkApplyResponse(
                    accepted_application_ids=[],
                    skipped=[],
                    applications=[],
                )
            )

        existing = request.app.state.store.get_for_user_by_ids(
            user_id=user_id,
            application_ids=normalized_ids,
            include_archived=True,
        )
        existing_by_id = {application.id: application for application in existing}
        eligible_statuses = {ApplicationStatus.review, ApplicationStatus.viewed}
        accepted_ids: list[str] = []
        accepted_jobs: list[ApplyTargetJob] = []
        skipped: list[BulkApplySkippedItem] = []

        for application_id in normalized_ids:
            application = existing_by_id.get(application_id)
            if application is None:
                skipped.append(BulkApplySkippedItem(application_id=application_id, reason="application_not_found"))
                continue
            if application.is_archived:
                skipped.append(BulkApplySkippedItem(application_id=application_id, reason="archived", status=application.status))
                continue
            if application.status not in eligible_statuses:
                skipped.append(BulkApplySkippedItem(application_id=application_id, reason="ineligible_status", status=application.status))
                continue
            accepted_ids.append(application_id)
            accepted_jobs.append(ApplyTargetJob(external_job_id=application.opportunity.id, title=application.opportunity.title, company=application.opportunity.company, apply_url=application.opportunity.url))

        if not accepted_ids:
            return _to_dict(BulkApplyResponse(accepted_application_ids=[], skipped=skipped, applications=[]))

        apply_run = request.app.state.orchestrator.start_apply_run(
            user_id=user_id,
            payload=ApplyRunStartRequest(jobs=accepted_jobs),
        )
        updated = request.app.state.store.update_status_for_user_application_ids(
            user_id=user_id,
            application_ids=accepted_ids,
            status=ApplicationStatus.applying,
        )
        return _to_dict(BulkApplyResponse(run_id=apply_run.run_id, status_url=apply_run.status_url, accepted_application_ids=accepted_ids, skipped=skipped, applications=updated))

    def resolve_mark_application_viewed(self, info, application_id: str):
        request = info.context["request"]
        user_id = authenticated_user_id_from_request(request)
        existing = request.app.state.store.get_for_user_by_ids(
            user_id=user_id,
            application_ids=[application_id],
            include_archived=True,
        )
        if not existing:
            raise GraphQLError("Application not found")
        if existing[0].is_archived:
            raise GraphQLError("Application is archived and cannot be updated")
        application = request.app.state.store.mark_viewed_for_user_application(
            user_id=user_id,
            application_id=application_id,
        )
        if application is None:
            raise GraphQLError("Application not found")
        return _to_dict(application)

    def resolve_mark_application_applied(self, info, application_id: str):
        request = info.context["request"]
        user_id = authenticated_user_id_from_request(request)
        existing = request.app.state.store.get_for_user_by_ids(
            user_id=user_id,
            application_ids=[application_id],
            include_archived=True,
        )
        if not existing:
            raise GraphQLError("Application not found")
        if existing[0].is_archived:
            raise GraphQLError("Application is archived and cannot be updated")
        application = request.app.state.store.mark_applied_for_user_application(
            user_id=user_id,
            application_id=application_id,
            submitted_at=utc_now(),
        )
        if application is None:
            raise GraphQLError("Application not found")
        return _to_dict(application)

    def resolve_update_profile(self, info, input):
        request = info.context["request"]
        user_id = authenticated_user_id_from_request(request)
        payload = ApplicationProfileUpsertRequest.model_validate(input)
        result = request.app.state.orchestrator.upsert_application_profile(user_id=user_id, payload=payload)
        return _to_dict(result)


schema = graphene.Schema(query=Query, mutation=Mutation)
