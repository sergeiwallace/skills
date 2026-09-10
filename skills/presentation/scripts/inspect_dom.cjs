const { createServer } = require('node:http')
const { readFile } = require('node:fs')
const { resolve, sep } = require('node:path')
const { chromium } = require('playwright')

const root = resolve(process.argv[2])
const slideIds = JSON.parse(process.argv[3])
const mime = {
  '.css': 'text/css',
  '.html': 'text/html',
  '.js': 'text/javascript',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
}

function suffix(path) {
  const match = path.match(/\.[^.]+$/)
  return match ? match[0] : ''
}

const server = createServer((request, response) => {
  const pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname)
  const relative = pathname === '/' ? 'index.html' : pathname.replace(/^\/+/, '')
  const file = resolve(root, relative)
  if (file !== root && !file.startsWith(root + sep)) {
    response.writeHead(403).end()
    return
  }
  readFile(file, (error, data) => {
    if (error) {
      if (!suffix(file)) {
        readFile(resolve(root, 'index.html'), (indexError, indexData) => {
          if (indexError)
            response.writeHead(404).end()
          else {
            response.setHeader('Content-Type', 'text/html')
            response.end(indexData)
          }
        })
        return
      }
      response.writeHead(404).end()
      return
    }
    response.setHeader('Content-Type', mime[suffix(file)] || 'application/octet-stream')
    response.end(data)
  })
})

function rgb(value) {
  const match = value.match(/rgba?\(([^)]+)\)/)
  if (match)
    return match[1].split(',').slice(0, 3).map(Number)
  const srgb = value.match(/color\(srgb\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)/)
  return srgb ? srgb.slice(1, 4).map(channel => Number(channel) * 255) : [255, 255, 255]
}

function luminance(color) {
  return color
    .map(value => value / 255)
    .map(value => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4)
    .reduce((sum, value, index) => sum + value * [0.2126, 0.7152, 0.0722][index], 0)
}

async function main() {
  await new Promise((accept, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', accept)
  })
  const { port } = server.address()
  const browser = await chromium.launch({ headless: true })
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } })
  const pages = []
  const elements = []
  const externalRequests = []
  page.on('request', request => {
    if (!request.url().startsWith(`http://127.0.0.1:${port}/`))
      externalRequests.push(request.url())
  })
  try {
    await page.goto(`http://127.0.0.1:${port}/`)
    await page.waitForTimeout(250)
    for (let index = 0; index < slideIds.length; index += 1) {
      if (index > 0)
        await page.keyboard.press('ArrowRight')
      await page.waitForTimeout(250)
      const found = await page.evaluate(({ id, index }) => {
        const root = [...document.querySelectorAll('[data-presentation-id]')]
          .find(element => element.dataset.presentationId === id)
        const slide = root?.closest('.slidev-page')
        if (!root || !slide)
          return null
        const style = getComputedStyle(root)
        const tokenValues = {
          background: style.getPropertyValue('--presentation-background').trim(),
          foreground: style.getPropertyValue('--presentation-foreground').trim(),
          accent: style.getPropertyValue('--presentation-accent').trim(),
          fontBody: style.getPropertyValue('--presentation-font-body').trim(),
          spacing: style.getPropertyValue('--presentation-space').trim(),
          radius: style.getPropertyValue('--presentation-radius').trim(),
        }
        const nodes = [...slide.querySelectorAll('*')]
          .filter(element =>
            element.children.length === 0
            && !element.closest('[aria-hidden=true]')
            && (element.textContent.trim()
              || element.tagName === 'IMG'
              || element.closest('a')?.getAttribute('href'))
            && getComputedStyle(element).visibility !== 'hidden')
          .map(element => {
            const bounds = element.getBoundingClientRect()
            const computed = getComputedStyle(element)
            let background = element
            while (background
              && getComputedStyle(background).backgroundColor === 'rgba(0, 0, 0, 0)')
              background = background.parentElement
            return {
              text: element.textContent.trim(),
              left: bounds.left,
              top: bounds.top,
              right: bounds.right,
              bottom: bounds.bottom,
              fontSize: Number.parseFloat(computed.fontSize),
              foreground: computed.color,
              background: background
                ? getComputedStyle(background).backgroundColor
                : 'rgb(255,255,255)',
              overflow: bounds.right > innerWidth || bounds.bottom > innerHeight,
              slideIndex: index,
              href: element.closest('a')?.getAttribute('href'),
              image: element.tagName === 'IMG' ? element.getAttribute('src') : undefined,
            }
          })
        return {
          page: {
            slideIndex: index,
            url: location.href,
            presentationId: root.dataset.presentationId,
            layout: root.dataset.presentationLayout,
            slots: [...root.querySelectorAll('[data-presentation-slot]')]
              .map(element => element.dataset.presentationSlot),
            tokenValues,
          },
          nodes,
        }
      }, { id: slideIds[index], index })
      if (!found)
        continue
      pages.push(found.page)
      for (const element of found.nodes) {
        const foreground = luminance(rgb(element.foreground))
        const background = luminance(rgb(element.background))
        element.contrast = (Math.max(foreground, background) + 0.05)
          / (Math.min(foreground, background) + 0.05)
        elements.push(element)
      }
    }
    process.stdout.write(JSON.stringify({ pages, elements, externalRequests }))
  }
  finally {
    await browser.close()
    await new Promise(resolveClose => server.close(resolveClose))
  }
}

main().catch(error => {
  console.error(error)
  server.close()
  process.exit(1)
})
