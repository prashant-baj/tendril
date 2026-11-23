/** Client API request/response for `POST /gardens/{gardenId}/media` (OB-02). */
export interface MediaUploadRequest {
  contentType: string;
  fileName: string;
  /** File size in bytes; photos are limited to 10 MB (ADR-0033). */
  sizeBytes: number;
}

export interface MediaUploadResponse {
  /** Presigned S3 PUT URL — upload directly to it, never through the Lambda. */
  uploadUrl: string;
  mediaId: string;
}
