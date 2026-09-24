FROM node:20-alpine

WORKDIR /app

# apps/web keeps its own npm lockfile (the Vercel build uses it too); the pnpm
# lockfile lives at the monorepo root, outside this build context.
COPY package.json package-lock.json ./
RUN npm ci --legacy-peer-deps

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
