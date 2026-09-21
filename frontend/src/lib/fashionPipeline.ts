import type { CustomerProfile, Garment, PipelinePreview } from '../types/vto'

export function getCustomerVideo(
  profile: CustomerProfile,
): { preview: string } | null {
  if (profile === 'female') return { preview: '/demo-videos/Female-VTO.mp4' }
  if (profile === 'male') return { preview: '/demo-videos/Male-VTO.mp4' }
  if (profile === 'female-senior') return { preview: '/demo-videos/Female-Senior-VTO.mp4' }
  if (profile === 'male-senior') return { preview: '/demo-videos/Male-Senior-VTO.mp4' }
  return null
}

function pipelineVideoFilename(profile: CustomerProfile): string | null {
  if (profile === 'female') return 'demo-videos/Female-VTO.mp4'
  if (profile === 'male') return 'demo-videos/Male-VTO.mp4'
  if (profile === 'female-senior') return 'demo-videos/Female-Senior-VTO.mp4'
  if (profile === 'male-senior') return 'demo-videos/Male-Senior-VTO.mp4'
  return null
}

// The pipeline stores uploaded garments under a generated filename, which
// carries no descriptive info for the styling-tips LLM — pass the catalog's
// real name/color/gender/brand through separately so tips match the garment.
function describeGarment(g: Garment): string {
  const genderLabel = g.gender && g.gender !== 'unisex' ? `${g.gender}'s ` : ''
  return `${genderLabel}${g.color} ${g.name} (${g.subcategory}, ${g.brand})`
}

async function uploadGarmentImage(garment: Garment): Promise<string> {
  const resp = await fetch(garment.image)
  if (!resp.ok) throw new Error(`Failed to fetch garment image: ${garment.image}`)
  const blob = await resp.blob()
  const ext = garment.image.split('.').pop() ?? 'jpg'
  const form = new FormData()
  form.append('file', blob, `${garment.id}.${ext}`)
  const upload = await fetch('/pipeline/upload/garment', { method: 'POST', body: form })
  if (!upload.ok) throw new Error(`Failed to upload garment image for ${garment.name}`)
  const data = (await upload.json()) as { filename: string }
  return data.filename
}

interface PipelineJobStatus {
  status: string
  stage?: string
  message?: string
}

export interface RunPipelineResult {
  jobId: string
  originalVideoUrl?: string
  outputVideoUrl: string
}

export interface RunPipelineOptions {
  sessionId: string
  customerProfile: CustomerProfile
  garments: Garment[]
  onStatus: (
    status: PipelinePreview['status'],
    message?: string,
    stage?: string,
  ) => void
}

export async function runFashionPipeline({
  sessionId,
  customerProfile,
  garments,
  onStatus,
}: RunPipelineOptions): Promise<RunPipelineResult> {
  const videoFilename = pipelineVideoFilename(customerProfile)
  if (!videoFilename) throw new Error('No customer video available for pipeline')

  onStatus('uploading', 'Preparing catalog images...')

  const garmentFilenames = await Promise.all(garments.map(uploadGarmentImage))

  const top = garments.find(g => g.overlayCategory === 'tops')
  const bottom = garments.find(g => g.overlayCategory === 'bottoms')
  const onePiece = garments.find(g => g.overlayCategory === 'one-pieces')

  const runBody: Record<string, unknown> = {
    session_id: sessionId,
    input_video: videoFilename,
  }

  if (onePiece) {
    runBody.input_garment = garmentFilenames[garments.indexOf(onePiece)]
    runBody.garment_category = 'one-pieces'
    runBody.styling_context = describeGarment(onePiece)
  } else if (top && bottom) {
    runBody.input_garment_top = garmentFilenames[garments.indexOf(top)]
    runBody.input_garment_bottom = garmentFilenames[garments.indexOf(bottom)]
    runBody.styling_context = `${describeGarment(top)} + ${describeGarment(bottom)}`
  } else if (top) {
    runBody.input_garment = garmentFilenames[garments.indexOf(top)]
    runBody.garment_category = 'tops'
    runBody.styling_context = describeGarment(top)
  } else if (bottom) {
    runBody.input_garment = garmentFilenames[garments.indexOf(bottom)]
    runBody.garment_category = 'bottoms'
    runBody.styling_context = describeGarment(bottom)
  } else {
    runBody.input_garment = garmentFilenames[0]
    runBody.garment_category = garments[0]?.overlayCategory ?? 'tops'
    if (garments[0]) runBody.styling_context = describeGarment(garments[0])
  }

  const runResp = await fetch('/pipeline/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(runBody),
  })
  if (!runResp.ok) {
    const err = (await runResp.json().catch(() => ({}))) as { detail?: string }
    throw new Error(err.detail ?? `Pipeline submit failed: ${runResp.status}`)
  }
  const runData = (await runResp.json()) as { job_id: string }
  const jobId = runData.job_id

  onStatus('queued', 'Pipeline job queued...')

  // Poll for completion
  while (true) {
    await new Promise<void>(resolve => setTimeout(resolve, 2000))
    const statusResp = await fetch(`/pipeline/jobs/${jobId}`)
    if (!statusResp.ok) continue

    const job = (await statusResp.json()) as PipelineJobStatus
    const status = job.status as PipelinePreview['status']
    onStatus(status, job.message, job.stage)

    if (status === 'done') {
      return {
        jobId,
        originalVideoUrl: getCustomerVideo(customerProfile)?.preview,
        outputVideoUrl: `/pipeline/outputs/${jobId}/slideshow.mp4`,
      }
    }
    if (status === 'failed') {
      throw new Error(job.message ?? 'Pipeline failed')
    }
  }
}

export async function clearFashionPipelineSession(sessionId: string): Promise<void> {
  await fetch(`/pipeline/sessions/${sessionId}/outputs`, { method: 'DELETE' })
}
