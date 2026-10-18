import { describe, it, expect } from 'vitest'
import { sourceFromDescription, validateFile, ApiError, isCancelled, safeSourceUrl } from './api'

describe('review input and evidence boundaries', () => {
  it('accepts only the case format and bounded nonempty files', () => {
    expect(validateFile({name:'test.MOV', size:1024}, 2048)).toBeNull()
    expect(validateFile({name:'test.mp4', size:1024}, 2048)).toBeTruthy()
    expect(validateFile({name:'test.mov', size:0}, 2048)).toBeTruthy()
    expect(validateFile({name:'test.mov', size:4096}, 2048)).toBeTruthy()
  })
  it('renders only recognized normative source links', () => {
    expect(sourceFromDescription('text https://adilet.zan.kz/rus/docs/V1900019680').url).toBe('https://adilet.zan.kz/rus/docs/V1900019680')
    expect(sourceFromDescription('text javascript:alert(1)').url).toBeUndefined()
    expect(sourceFromDescription('text https://adilet.zan.kz.evil.test/doc').url).toBeUndefined()
    expect(sourceFromDescription('text https://elsewhere.test/doc').url).toBeUndefined()
  })
  it('preserves the distinction between request failure and a negative verdict', () => {
    const failure = new ApiError(503, 'Занята очередь')
    expect(failure.status).toBe(503)
    expect('verdict' in failure).toBe(false)
  })
  it('distinguishes user cancellation from technical failure and negative verdict', () => {
    expect(isCancelled({job_id:'x',status:'failed',error:'Analysis cancelled by user'})).toBe(true)
    expect(isCancelled({job_id:'x',status:'failed',error:'Inference failed'})).toBe(false)
    expect(isCancelled({job_id:'x',status:'done',verdict:'no_violation',violations:[]})).toBe(false)
  })

  it('allows only official HTTPS links for structured normative references', () => {
    expect(safeSourceUrl('https://adilet.zan.kz/rus/docs/V1900019680')).toBeTruthy()
    expect(safeSourceUrl('javascript:alert(1)')).toBeUndefined()
    expect(safeSourceUrl('https://adilet.zan.kz.fake.test/')).toBeUndefined()
    expect(safeSourceUrl('http://adilet.zan.kz/rus/docs/V1900019680')).toBeUndefined()
  })

})
