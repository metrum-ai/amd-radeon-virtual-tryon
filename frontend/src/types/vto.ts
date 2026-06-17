// Copyright Advanced Micro Devices, Inc.
// 
// SPDX-License-Identifier: MIT

export interface Garment {
  id: string
  name: string
  category: string
  overlayCategory: 'tops' | 'bottoms' | 'one-pieces'
  gender?: 'women' | 'men' | 'unisex'
  subcategory: string
  brand: string
  color: string
  price: number
  gradient: string
  image: string
  tags: string[]
}

export interface RecentTryOn {
  garment: Garment
  triedAt: string
  jobId?: string
  keyframeUrl?: string
  outputVideoUrl?: string
}

export type CustomerProfile = '' | 'female' | 'male' | 'female-senior' | 'male-senior'

export interface PipelinePreview {
  status: 'idle' | 'uploading' | 'queued' | 'running' | 'done' | 'failed'
  jobId?: string
  originalVideoUrl?: string
  outputVideoUrl?: string
  message?: string
  stage?: string
}

export interface PipelineCompletion {
  jobId: string
  garment: Garment
  outputVideoUrl: string
}

export interface Message {
  id: string
  role: 'agent' | 'user'
  content: string
  timestamp: string
  agent?: string
}
