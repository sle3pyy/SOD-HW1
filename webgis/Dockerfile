FROM node:24-alpine AS dependencies

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM dependencies AS development
COPY . ./
EXPOSE 5173
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]

FROM dependencies AS frontend-build
COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src
RUN npm run build

FROM dependencies AS provisioner-build
COPY configure_geoserver.ts tsconfig.provisioner.json ./
RUN npm run build:provisioner

FROM node:24-alpine AS geoserver-config
WORKDIR /app
COPY --from=provisioner-build /app/dist-provisioner/configure_geoserver.js ./configure_geoserver.js
ENTRYPOINT ["node", "/app/configure_geoserver.js"]

FROM nginx:alpine AS production
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=frontend-build /app/dist /usr/share/nginx/html
EXPOSE 80
