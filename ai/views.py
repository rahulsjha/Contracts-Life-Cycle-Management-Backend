from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.http import StreamingHttpResponse
from .models import AIInferenceModel, DraftGenerationTask, ClauseAnchor
from .serializers import AIInferenceSerializer, DraftGenerationTaskSerializer, ClauseAnchorSerializer
from .tasks import generate_draft_async
from .pii_protection import PIIScrubber, ScrubberAuditLog
from django.utils import timezone
import uuid
import logging
import json
import os
from repository.models import Document
from repository.embeddings_service import VoyageEmbeddingsService
import google.generativeai as genai
from django.conf import settings
import numpy as np
from contracts.models import Clause

logger = logging.getLogger(__name__)

# Configure Gemini
_raw_gemini_api_key = (getattr(settings, 'GEMINI_API_KEY', '') or '')
_normalized_gemini_api_key = ''.join(_raw_gemini_api_key.split())
if _normalized_gemini_api_key:
    genai.configure(api_key=_normalized_gemini_api_key)


class AIViewSet(viewsets.ViewSet):
    """
    AI Endpoints for draft generation, metadata extraction, and classification
    """
    permission_classes = [IsAuthenticated]
    throttle_scope = 'ai'

    @action(detail=False, methods=['post'], url_path='generate/draft')
    def generate_draft(self, request):
        """
        Endpoint 3: Draft Generation (Async)
        POST /api/v1/ai/generate/draft/
        """
        contract_type = request.data.get('contract_type')
        input_params = request.data.get('input_params', {})
        template_id = request.data.get('template_id')

        if not contract_type:
            return Response(
                {'error': 'contract_type is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Create task record in DB
        task = DraftGenerationTask.objects.create(
            tenant_id=request.user.tenant_id,
            user_id=request.user.user_id,
            contract_type=contract_type,
            input_params=input_params,
            template_id=template_id,
            task_id=str(uuid.uuid4()),  # Placeholder, will be updated by celery
            status='pending'
        )

        # Queue Celery task
        celery_task = generate_draft_async.delay(
            task_id=str(task.id),
            tenant_id=str(request.user.tenant_id),
            contract_type=contract_type,
            input_params=input_params,
            template_id=template_id
        )
        
        # Update task with actual Celery task ID
        task.task_id = celery_task.id
        task.save()

        serializer = DraftGenerationTaskSerializer(task)
        return Response(serializer.data, status=status.HTTP_202_ACCEPTED)

    @action(detail=False, methods=['get'], url_path='generate/status/(?P<task_id>[^/.]+)')
    def get_draft_status(self, request, task_id=None):
        """
        GET /api/v1/ai/generate/status/{task_id}/
        """
        try:
            task = DraftGenerationTask.objects.get(
                task_id=task_id,
                tenant_id=request.user.tenant_id
            )
            serializer = DraftGenerationTaskSerializer(task)
            return Response(serializer.data)
        except DraftGenerationTask.DoesNotExist:
            return Response(
                {'error': 'Task not found'},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=False, methods=['post'], url_path='extract/metadata')
    def extract_metadata(self, request):
        """
        Endpoint 4: Metadata Extraction
        POST /api/v1/ai/extract/metadata/
        """
        text = request.data.get('text')
        if not text:
            return Response({'error': 'text is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Scrub PII before sending to Gemini
            scrubbed_text, redactions = PIIScrubber.scrub(text)
            
            # Log the scrubbing operation
            ScrubberAuditLog.log_scrub_operation(
                user_id=str(request.user.user_id),
                tenant_id=str(request.user.tenant_id),
                text_length=len(text),
                redactions=redactions,
                operation_type='metadata_extraction'
            )
            
            if redactions:
                logger.info(f"Scrubbed {sum(len(v) for v in redactions.values())} PII instances before metadata extraction")

            model = genai.GenerativeModel('gemini-2.0-flash')
            
            prompt = f"""
            Extract the following metadata from this contract text.
            Return the output as a valid JSON object with the specified keys.

            Schema:
            {{
                "parties": [
                    {{"name": "string", "role": "string (e.g., 'Landlord', 'Tenant')"}},
                    {{"name": "string", "role": "string"}}
                ],
                "effective_date": "YYYY-MM-DD or null",
                "termination_date": "YYYY-MM-DD or null",
                "contract_value": {{
                    "amount": "number or null",
                    "currency": "string (e.g., 'USD') or null"
                }}
            }}

            Contract Text:
            ---
            {scrubbed_text[:8000]}
            ---
            """
            
            response = model.generate_content(prompt)
            
            # Basic parsing and validation
            import json
            try:
                # Gemini often wraps JSON in ```json ... ```
                clean_response = response.text.strip().replace('```json', '').replace('```', '').strip()
                extracted_data = json.loads(clean_response)
                return Response({'metadata': extracted_data}, status=status.HTTP_200_OK)
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to parse Gemini response for metadata extraction: {e}")
                return Response({'error': 'Failed to parse AI response', 'details': response.text}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        except Exception as e:
            logger.error(f"Metadata extraction error: {e}")
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    @action(detail=False, methods=['post'], url_path='classify/clause')
    def classify_clause(self, request):
        """
        Endpoint 5: Clause Classification
        POST /api/v1/ai/classify/clause/
        """
        clause_text = request.data.get('clause_text')
        if not clause_text:
            return Response({'error': 'clause_text is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            # Get all active anchor clauses with embeddings
            anchors = ClauseAnchor.objects.filter(is_active=True, embedding__isnull=False)
            if not anchors.exists():
                return Response({'error': 'No anchor clauses configured for classification'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Generate embedding for input text
            embeddings_service = VoyageEmbeddingsService()
            query_embedding = embeddings_service.embed_query(clause_text)

            if not query_embedding:
                return Response({'error': 'Failed to generate embedding for input text'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            query_vec = np.array(query_embedding, dtype=np.float32)
            query_norm = np.linalg.norm(query_vec)

            scores = []
            for anchor in anchors:
                anchor_vec = np.array(anchor.embedding, dtype=np.float32)
                anchor_norm = np.linalg.norm(anchor_vec)
                
                if anchor_norm > 0 and query_norm > 0:
                    similarity = np.dot(query_vec, anchor_vec) / (query_norm * anchor_norm)
                    scores.append({
                        'label': anchor.label,
                        'category': anchor.category,
                        'confidence': float(similarity)
                    })

            if not scores:
                return Response({'error': 'Could not classify clause'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Find nearest anchor by cosine distance
            best_match = max(scores, key=lambda x: x['confidence'])

            return Response(best_match, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Clause classification error: {e}")
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='generate/template-stream')
    def generate_template_stream(self, request):
        """Stream AI-generated template edits as Server-Sent Events (SSE).

        POST /api/v1/ai/generate/template-stream/

        Body:
          - prompt: string (required)
          - current_text: string (required)
          - contract_type: string (optional)

        Emits SSE events:
          - event: delta, data: {"delta": "..."}
          - event: done,  data: {"ok": true}
          - event: error, data: {"error": "..."}
        """

        prompt = (request.data.get('prompt') or '').strip()
        current_text = request.data.get('current_text')
        contract_type = (request.data.get('contract_type') or '').strip() or None
        messages = request.data.get('messages')

        if not prompt:
            return Response({'error': 'prompt is required'}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(current_text, str) or not current_text.strip():
            return Response({'error': 'current_text is required'}, status=status.HTTP_400_BAD_REQUEST)

        if not _normalized_gemini_api_key:
            return Response({'error': 'GEMINI_API_KEY is not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        model_name = getattr(settings, 'GEMINI_CONTRACT_EDIT_MODEL', None) or 'gemini-2.5-pro'

        # Track AI Builder generations for dashboard insights.
        # The template-based AI editor uses this endpoint (streaming edits), so we
        # create a DraftGenerationTask record per request.
        task = None
        try:
            task = DraftGenerationTask.objects.create(
                tenant_id=request.user.tenant_id,
                user_id=request.user.user_id,
                contract_type=(contract_type or 'template_edit'),
                input_params={
                    'mode': 'template_stream',
                    'prompt_length': len(prompt),
                    'current_text_length': len(current_text or ''),
                    'model': model_name,
                },
                template_id=None,
                task_id=f"sse-{uuid.uuid4()}",
                status='processing',
                started_at=timezone.now(),
            )
        except Exception:
            task = None

        # Retrieve relevant clauses using Voyage Law-2.
        def _pick_relevant_clauses() -> list[dict]:
            try:
                embeddings_service = VoyageEmbeddingsService()
                query_embedding = embeddings_service.embed_query(prompt)
                if not query_embedding:
                    return []

                qs = Clause.objects.filter(tenant_id=request.user.tenant_id, status='published')
                if contract_type:
                    qs = qs.filter(contract_type=contract_type)

                items = list(qs.order_by('-updated_at')[:40])
                if not items:
                    return []

                texts = [f"{c.name}\n\n{c.content}"[:8000] for c in items]
                embeddings = embeddings_service.embed_batch(texts)

                q = np.array(query_embedding, dtype=np.float32)
                qn = float(np.linalg.norm(q))
                if qn <= 0:
                    return []

                scored: list[tuple[float, Clause]] = []
                for clause, emb in zip(items, embeddings):
                    if not emb:
                        continue
                    v = np.array(emb, dtype=np.float32)
                    vn = float(np.linalg.norm(v))
                    if vn <= 0:
                        continue
                    sim = float(np.dot(q, v) / (qn * vn))
                    scored.append((sim, clause))

                scored.sort(key=lambda x: x[0], reverse=True)
                out = []
                for sim, clause in scored[:5]:
                    out.append(
                        {
                            'clause_id': clause.clause_id,
                            'name': clause.name,
                            'similarity': round(sim, 4),
                            'content': clause.content,
                        }
                    )
                return out
            except Exception:
                return []

        relevant = _pick_relevant_clauses()
        clauses_block = ''
        if relevant:
            blocks = []
            for c in relevant:
                blocks.append(f"- [{c['clause_id']}] {c['name']} (sim={c['similarity']})\n{c['content']}")
            clauses_block = "\n\n".join(blocks)[:12000]

        base_text = (current_text or '')[:20000]
        user_instruction = prompt[:4000]

        def _format_history(messages_obj) -> str:
            """Format prior chat messages for context (safe + size-bounded)."""
            if not isinstance(messages_obj, list):
                return ''

            cleaned = []
            for m in messages_obj[-24:]:
                if not isinstance(m, dict):
                    continue
                role = str(m.get('role') or '').strip().lower()
                content = m.get('content')
                if role not in ('user', 'assistant', 'system'):
                    continue
                if not isinstance(content, str):
                    continue
                text = content.strip()
                if not text:
                    continue
                # Avoid dumping huge content into the prompt.
                cleaned.append((role, text[:1200]))

            if not cleaned:
                return ''

            lines = []
            for role, text in cleaned:
                tag = 'User' if role == 'user' else 'Assistant' if role == 'assistant' else 'System'
                lines.append(f"{tag}: {text}")
            return "\n".join(lines)[:12000]

        history_block = _format_history(messages)

        generation_prompt = f"""
You are a senior legal contract drafting assistant.

Task:
- Apply the user's instruction to the existing contract text.
- Keep the contract coherent and legally styled.
- Preserve headings/numbering where possible.
- If the user asks to insert a clause, add it in the most appropriate section.
- If the user asks to update a term, change it consistently everywhere.

Output rules:
- Return ONLY the full revised contract as plain text.
- Do not include markdown fences, commentary, or JSON.

User instruction:
{user_instruction}

Conversation context (for reference):
---
{history_block or '(none)'}
---

Existing contract text:
---
{base_text}
---

Relevant clause library (optional):
---
{clauses_block or '(none)'}
---
""".strip()

        def event_stream():
            try:
                payload = {'model': model_name}
                if task is not None:
                    payload['task_id'] = task.task_id
                logger.info(f"[SSE] Emitting meta event with task_id={payload.get('task_id')}, model={model_name}")
                yield f"event: meta\ndata: {json.dumps(payload)}\n\n"
            except Exception as e:
                logger.exception(f"[SSE] Error yielding meta event: {e}")
                pass

            def _extract_text_from_chunk(chunk) -> str:
                """Extract text from a streamed Gemini chunk without relying on quick accessors.

                Some library versions raise when accessing `.text` if no valid Part exists
                (e.g., safety-blocked responses). We treat those chunks as empty.
                """
                # 1) Try the common accessor, but it may raise.
                try:
                    t = chunk.text  # type: ignore[attr-defined]
                    if isinstance(t, str) and t:
                        return t
                except Exception:
                    pass

                # 2) Fallback: walk candidates/parts.
                try:
                    candidates = getattr(chunk, 'candidates', None) or []
                    for cand in candidates:
                        content = getattr(cand, 'content', None)
                        parts = getattr(content, 'parts', None) or []
                        for part in parts:
                            pt = getattr(part, 'text', None)
                            if isinstance(pt, str) and pt:
                                return pt
                except Exception:
                    pass

                return ''

            def _safety_summary_from_chunk(chunk) -> str:
                try:
                    candidates = getattr(chunk, 'candidates', None) or []
                    if not candidates:
                        return ''

                    # Collect category:probability style info when available.
                    bits: list[str] = []
                    for cand in candidates:
                        ratings = getattr(cand, 'safety_ratings', None) or []
                        for r in ratings:
                            cat = getattr(r, 'category', None)
                            prob = getattr(r, 'probability', None)
                            # category/probability can be enums; stringify safely.
                            if cat is not None and prob is not None:
                                bits.append(f"{cat}:{prob}")
                    # De-dupe while preserving order.
                    seen = set()
                    out: list[str] = []
                    for b in bits:
                        if b in seen:
                            continue
                        seen.add(b)
                        out.append(b)
                    return ', '.join(out[:12])
                except Exception:
                    return ''

            try:
                model = genai.GenerativeModel(model_name)
                logger.info(f"[SSE] Starting bounded Gemini generation with model={model_name}")
                timeout_s = int(os.getenv('GEMINI_STREAM_TIMEOUT_SECONDS', '50'))
                response = model.generate_content(generation_prompt, request_options={'timeout': timeout_s})
                full_text = getattr(response, 'text', None) or ''

                if not full_text.strip():
                    safety = _safety_summary_from_chunk(response)
                    msg = 'AI returned no text output.'
                    if safety:
                        msg = 'AI response appears to be blocked by safety filters.'
                        msg += f" Safety ratings: {safety}."
                    logger.error(f"[SSE] {msg}")
                    raise RuntimeError(msg)

                # Emit deterministic chunks so UI sees progressive updates even if provider
                # does not stream token-by-token reliably.
                chunk_size = int(os.getenv('SSE_DELTA_CHUNK_SIZE', '700'))
                offset = 0
                chunk_count = 0
                while offset < len(full_text):
                    piece = full_text[offset: offset + chunk_size]
                    offset += chunk_size
                    chunk_count += 1
                    yield f"event: delta\ndata: {json.dumps({'delta': piece})}\n\n"
                logger.info(f"[SSE] Emitted {chunk_count} deterministic delta chunks")

                if task is not None:
                    try:
                        task.status = 'completed'
                        task.completed_at = timezone.now()
                        task.citations = [c.get('clause_id') for c in (relevant or []) if c.get('clause_id')]
                        task.save(update_fields=['status', 'completed_at', 'citations', 'updated_at'])
                    except Exception:
                        pass
                yield f"event: done\ndata: {json.dumps({'ok': True})}\n\n"
            except GeneratorExit:
                # Client disconnected early; keep status as-is (processing) so
                # the generation is still counted for usage KPIs.
                logger.warning("[SSE] GeneratorExit: client disconnected early")
                raise
            except Exception as e:
                logger.exception(f"[SSE] Exception in event_stream: {type(e).__name__}: {e}")
                if task is not None:
                    try:
                        task.status = 'failed'
                        task.error_message = str(e)[:4000]
                        task.completed_at = timezone.now()
                        task.save(update_fields=['status', 'error_message', 'completed_at', 'updated_at'])
                    except Exception:
                        pass
                # Return a clean error message to the client (avoid leaking SDK internals).
                raw = str(e) if e else 'AI generation failed'
                cleaned = raw
                if 'response.text' in raw and 'candidate.safety_ratings' in raw:
                    cleaned = 'AI response was blocked by safety filters. Try rephrasing your instruction.'
                logger.info(f"[SSE] Emitting error event: {cleaned}")
                yield f"event: error\ndata: {json.dumps({'error': cleaned})}\n\n"

        resp = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
        resp['Cache-Control'] = 'no-cache'
        resp['X-Accel-Buffering'] = 'no'
        resp['Connection'] = 'keep-alive'
        return resp

