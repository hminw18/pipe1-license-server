# Pipe1 Training Data Upload Requirements

## 1. Purpose

This document defines requirements for sending report-based training data from the Pipe1 desktop app to a server.

Primary training objective:

- Train automatic classification for captured CCTV inspection images.
- Predict condition/status labels and defect/anomaly labels from capture images.

The first target model is not full-video analysis. It is image-based classification using the defect capture images already created while generating inspection reports.

## 2. Product Context

Current Pipe1 data flow:

```text
Report
  - one video
  - report metadata
  - pipe/manhole information
  - actual survey information
  - multiple captured defect/condition rows
  - Excel/PDF report outputs
```

Training upload should use the same report data snapshot that was used to generate the report.

Trigger:

- When a report is successfully generated.
- Report generation means Excel report generation or PDF visual report generation.
- Upload must start only after the local report generation succeeds.

The local report generation result must never depend on training-data upload success.

## 3. Scope

### 3.1 In Scope for MVP

- Capture image upload.
- Label upload based on report defect rows.
- Minimal report/pipe context needed for model training.
- Upload queue with retry.
- Upload status tracking.
- Duplicate prevention when the same report snapshot is generated multiple times.
- User-visible or license-level training-data consent.
- Server-side dataset intake API.

### 3.2 Out of Scope for MVP

- Full video upload.
- Raw Excel/PDF report upload.
- Cloud sync of the user's full local workspace.
- Real-time upload while a user is capturing defects.
- Automatic model inference inside the desktop app.
- Web portal review UI for uploaded samples.

Future versions may add:

- Full-video upload with separate high-risk consent.
- Human review and relabeling portal.
- Dataset versioning UI.
- AI-assisted defect suggestion in the desktop app.

## 4. Upload Principle

Training upload should be report-snapshot based.

When the user generates a report, the app should capture the report's current data state and create an upload job. The upload job should contain the labels and metadata that correspond to the exact capture images used by that report at that time.

Rules:

- Upload should be automatic only if training-data consent is enabled.
- Upload should be asynchronous.
- Report generation should remain fast and local.
- Upload failure should not block Excel/PDF creation.
- Upload retry should not require the user to regenerate the report.
- Re-generating the same unchanged report should not upload duplicate samples.
- Re-generating a changed report should create a new dataset snapshot version.

## 5. Consent and Legal Requirements

Training data upload requires explicit consent.

Consent must be separate from:

- Product license activation.
- Required service terms.
- Error/crash diagnostics.

Consent levels:

```text
none
  - Do not upload training data.

capture_images_and_labels
  - Upload capture images and training labels.
  - Do not upload original videos.
  - Do not upload generated Excel/PDF files.

extended_report_context
  - Upload capture images, labels, and selected report context.
  - Still do not upload original videos unless a later full-video consent exists.
```

MVP default:

- `capture_images_and_labels`
- Full video upload disabled.
- Generated Excel/PDF upload disabled.

Consent record should include:

- consent type
- consent version
- accepted or revoked state
- accepted timestamp
- revoked timestamp, if any
- license id or organization id
- device id
- app version

The app must provide a visible setting to show whether training-data upload is enabled.

## 6. Data Minimization

The training objective is image classification. Therefore, the default payload should avoid sending unnecessary customer-identifying information.

Do not upload by default:

- source video file
- generated Excel file
- generated PDF file
- project name
- business name
- client name
- road address
- lot number
- manhole latitude/longitude
- inspector name
- contractor name
- freeform memo, unless explicitly approved

Upload by default:

- capture image
- defect/condition labels
- non-identifying report context useful for model training
- technical metadata needed for traceability

Allowed non-identifying context:

- item category: `맨홀`, `관로`, `암거`
- pipe type
- pipe category
- specification
- drive direction
- distance in meters
- timestamp in video
- grade, if defect row has a defect grade
- quadrant
- anonymized report snapshot id
- app version

If extended report context is enabled, the payload may include selected report and pipe fields. Extended context must be documented in the legal consent copy.

## 7. Training Dataset Model

### 7.1 Dataset Snapshot

Each successful report generation can create one training dataset snapshot.

Suggested fields:

- `snapshot_id`
- `local_report_id`
- `report_fingerprint`
- `export_type`: `excel`, `pdf`
- `generated_at`
- `app_version`
- `license_id`
- `organization_id`, optional
- `device_id`
- `workspace_id`, generated local id
- `sample_count`
- `payload_schema_version`
- `consent_version`

`report_fingerprint` should be a hash derived from:

- report id
- relevant report fields
- pipe/manhole fields included in upload
- defect row ids
- defect labels
- capture image hashes

The fingerprint is used to prevent duplicate upload of unchanged report snapshots.

### 7.2 Training Sample

Each defect/condition row with a valid capture image becomes a training sample.

Suggested fields:

- `sample_id`
- `snapshot_id`
- `local_defect_id`
- `capture_image_sha256`
- `capture_image_filename`
- `image_width`
- `image_height`
- `timestamp_ms`
- `distance_m`
- `drive_direction`
- `item_category`
- `sample_type`: `condition`, `defect`, `unlabeled`
- `condition_item`
- `defect_item`
- `defect_type`: `구조`, `운영`, optional
- `grade`: `대`, `중`, `소`, optional
- `score`, optional
- `quadrant`, optional
- `manhole_defect_depth_m`, optional
- `pipe_type`, optional
- `category`, optional
- `specification`, optional
- `quality_flags`

`sample_type` rules:

- `condition`: `condition_item` exists and `defect_item` is empty.
- `defect`: `defect_item` exists.
- `unlabeled`: neither condition nor defect label is available.

MVP should upload only `condition` and `defect` samples.

`unlabeled` samples should be skipped unless a later active-learning workflow is added.

## 8. Label Requirements

Labels must match the defect taxonomy.

Required labels for condition/status classification:

- `item_category`
- `condition_item`

Required labels for defect/anomaly classification:

- `item_category`
- `defect_item`
- `defect_type`
- `grade`, if valid for that defect item

Optional labels:

- `quadrant`
- `drive_direction`
- `distance_m`
- `manhole_defect_depth_m`
- `score`

Rules:

- Invalid taxonomy combinations must not be uploaded as valid labels.
- Missing capture images must be skipped and logged in the upload job.
- If a defect item has only one valid grade, upload the auto-selected grade.
- If a report row is changed later, the next generated report should create a new snapshot.
- Training labels are based on the user's final saved report data at report generation time.

## 9. Upload Timing

Upload job creation timing:

1. User clicks Excel or PDF report generation.
2. App saves current report details locally.
3. App generates the local report file.
4. If local generation succeeds, app creates a training dataset snapshot.
5. App adds the snapshot to the local upload queue.
6. App uploads in the background if network and consent allow it.

If both Excel and PDF are generated from the same unchanged report:

- The app should not upload duplicate samples.
- The app may record both export events against the same dataset snapshot.

If report generation fails:

- Do not create a training upload snapshot.
- Do not upload partial data.

## 10. Local Upload Queue

The desktop app must persist upload jobs locally.

Suggested local tables:

### `training_upload_snapshots`

- `id`
- `report_id`
- `report_fingerprint`
- `export_type`
- `status`: `pending`, `uploading`, `uploaded`, `failed`, `skipped`, `cancelled`
- `sample_count`
- `uploaded_sample_count`
- `server_snapshot_id`
- `last_error`
- `created_at`
- `updated_at`
- `uploaded_at`

### `training_upload_samples`

- `id`
- `snapshot_id`
- `defect_id`
- `image_path`
- `image_sha256`
- `payload_json`
- `status`: `pending`, `uploaded`, `failed`, `skipped`
- `server_sample_id`
- `last_error`
- `created_at`
- `updated_at`

Rules:

- Queue state must survive app restart.
- Upload should retry with backoff.
- Upload should be resumable at sample level.
- The queue must not delete local capture images.
- The queue should skip samples whose image file no longer exists.
- The queue should keep enough error information for support but must not log secrets.

## 11. Server API Requirements

MVP API:

```text
POST /training/snapshots
POST /training/snapshots/{snapshot_id}/samples
POST /training/snapshots/{snapshot_id}/complete
GET  /training/snapshots/{snapshot_id}
```

Optional large-file API:

```text
POST /training/uploads/presign
PUT  object storage upload URL
POST /training/samples/{sample_id}/attach-image
```

Recommended MVP upload strategy:

- Use direct multipart upload to the API server for small/medium capture images.
- Move image binaries to object storage when upload volume grows.

API requirements:

- HTTPS only.
- Authorization through activated license/device credential or account token.
- Validate entitlement feature: `training_upload`.
- Validate consent status before accepting samples.
- Validate file type and image dimensions.
- Validate image checksum.
- Validate taxonomy labels server-side.
- Reject duplicate snapshot fingerprints for the same license/device unless explicitly versioned.
- Return stable server ids for snapshots and samples.

## 12. Server Storage Requirements

Server should store:

- organization/license/device references
- dataset snapshot metadata
- training sample metadata
- image file or object storage key
- image checksum
- label schema version
- consent version
- app version
- received timestamp

Server should not rely on local desktop ids as globally unique ids.

Server should keep a mapping from:

```text
license_id + device_id + local_report_id + report_fingerprint
```

to the server-side dataset snapshot.

## 13. Privacy and Security Requirements

Security rules:

- Use HTTPS.
- Do not upload license keys in training-data API calls.
- Do not log image contents or full metadata payloads by default.
- Do not include local absolute file paths in server payloads.
- Do not include access tokens in logs.
- Use request timeouts.
- Limit max image size.
- Rate-limit upload APIs.

Privacy rules:

- Avoid customer-identifying fields by default.
- Do not upload raw source videos in MVP.
- Do not upload generated reports in MVP.
- Do not upload GPS coordinates by default.
- Do not upload freeform memo by default.
- Allow consent revocation for future uploads.
- Revocation does not automatically delete already uploaded data unless legal policy says so; deletion request handling must be documented.

## 14. Desktop UX Requirements

The app should keep the upload UX quiet but visible.

Required UI:

- Training upload enabled/disabled state.
- Last upload status for current report.
- Pending/failed upload count.
- Retry failed uploads action.
- Disable training upload action or setting when the license does not include `training_upload`.

Report generation UX:

- After report generation, show local report success as the primary message.
- Do not show upload failure as report generation failure.
- If upload is queued, optional status text may say `학습 데이터 업로드 대기 중`.
- If upload fails, show it in a non-blocking status area or settings screen.

## 15. Failure Handling

Network unavailable:

- Keep upload job as `pending`.
- Retry later.

Consent disabled:

- Mark snapshot as `skipped`.
- Do not create sample upload records.

License invalid or feature disabled:

- Mark snapshot as `skipped` or `failed` with clear reason.
- Do not block local report generation.

Image file missing:

- Mark sample as `skipped`.
- Continue uploading other samples.

Server rejects label:

- Mark sample as `failed`.
- Store validation error.
- Do not retry until report data changes or app version changes.

## 16. MVP Implementation Phases

### Phase 1 - Local Snapshot and Queue

- Add local upload queue tables.
- Build report fingerprint generator.
- Build report snapshot payload builder.
- Build sample payload builder.
- Add duplicate prevention.

### Phase 2 - Server Intake API

- Add training snapshot and sample endpoints.
- Add server-side taxonomy validation.
- Add image checksum validation.
- Add storage for uploaded images and metadata.

### Phase 3 - Desktop Upload Worker

- Add background upload service.
- Add retry/backoff.
- Add status UI.
- Add failure logging.

### Phase 4 - Consent and Legal Hardening

- Add training upload consent screen/settings.
- Add consent version tracking.
- Add privacy policy text for uploaded fields.
- Add deletion request policy.

## 17. Open Decisions

- Should upload trigger on Excel generation, PDF generation, or both?
- If both, which export type should be treated as the canonical report-generation event?
- Should report generation automatically upload when consent exists, or should the first version ask once after generation?
- Which report metadata fields improve classification enough to justify upload?
- Should `memo` ever be uploaded?
- Should captures for `이상없음` and other condition-only rows be uploaded?
- Should server accept samples with no grade?
- Should uploaded images be resized/compressed before upload?
- What is the retention period for uploaded images?
- What is the deletion policy if a customer withdraws training-data consent?

