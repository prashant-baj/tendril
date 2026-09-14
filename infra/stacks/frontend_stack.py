"""Frontend stack: S3 static website hosting for the Angular PWA.

Plain S3 static-website hosting (public read), per ADR-0010 — chosen over S3+CloudFront
for simplicity/cost. Known trade-off (documented in the ADR): S3 website endpoints are
HTTP-only, so the PWA's service worker won't register in production until this sits
behind HTTPS (e.g. CloudFront) later.

All resources are env-prefixed. Prod retains the bucket; dev is destroyable. See
docs/architecture/ADRs/0003-frontend-angular-and-design-system.md and 0010.
"""

from aws_cdk import (
    CfnOutput,
    RemovalPolicy,
    Stack,
    aws_s3 as s3,
)
from constructs import Construct


class FrontendStack(Stack):
    def __init__(self, scope: Construct, cid: str, *, env_name: str, **kwargs) -> None:
        super().__init__(scope, cid, **kwargs)
        prefix = f"tendril-{env_name}"
        retain = env_name == "prod"
        removal = RemovalPolicy.RETAIN if retain else RemovalPolicy.DESTROY

        # Angular Router uses HTML5 pushState — every unknown path (deep links like
        # /tasks or /goals/g1) must fall back to index.html so the client-side router
        # can take over. S3 website hosting's error document does exactly that (the
        # response reports 404, but the Angular app still boots and renders the route).
        self.web_bucket = s3.Bucket(
            self,
            "WebBucket",
            bucket_name=f"{prefix}-web",
            website_index_document="index.html",
            website_error_document="index.html",
            public_read_access=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ACLS,
            removal_policy=removal,
            auto_delete_objects=not retain,
        )

        CfnOutput(self, "WebBucketName", value=self.web_bucket.bucket_name)
        CfnOutput(self, "WebsiteUrl", value=self.web_bucket.bucket_website_url)
