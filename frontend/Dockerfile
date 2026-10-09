# Étape 1 : build Vite. Étape 2 : Nginx sert les fichiers statiques et
# relaie /api vers le backend (même domaine, cf. nginx.conf).
# Même version majeure de Node/npm que celle qui génère package-lock.json (npm 11).
FROM node:24-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Vide = URLs relatives (/api/...) : frontend et API servis par le même domaine.
ENV VITE_API_BASE_URL=""
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD wget -qO- http://127.0.0.1/ >/dev/null || exit 1
