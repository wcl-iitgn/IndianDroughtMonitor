document.addEventListener("DOMContentLoaded", init);

// Canvas contexts
const C_raster = document.getElementById("C_raster");
const c_raster = C_raster.getContext("2d");
const C_vector = document.getElementById("C_vector");
const c_vector = C_vector.getContext("2d");

// Application Configuration and State
const state = {
    gridCDI: null,       // Matrix holding the climate data values
    totalRows: 0, 
    totalCols: 0,
    dataStep: 0.25,
    minVal: 0, 
    maxVal: 1,
    
    // Geographic Boundaries (Base Reference)
    base: { lat_W: 68.0, lat_E: 97.5, long_N: 37.0, long_S: 7.0 },
    
    // Current Viewport Boundaries (Changes on Zoom)
    lat_W: 68.0, lat_E: 97.5, long_N: 37.0, long_S: 7.0,

    // Layout dimensions
    margin: { top: 20, right: 20, bottom: 20, left: 20 },
    plotWidth: 0, 
    plotHeight: 0,

    // Drag-to-Zoom Selection Box State
    isSelecting: false,
    startSelectX: 0, startSelectY: 0,
    currentSelectX: 0, currentSelectY: 0,

    hoverCoords: null,

    selectedStateId: null,

    gridState: null,
    stateStep: 0.0625,
    stateRows: 0,
    stateCols: 0,

    mainlandBoundary: [],
    stateVectorBoundaries: [],

    // Place this inside your existing const state = { ... } object definition:
    isAnimating: false,
    currentAnimationDateStr: null,

    INTERP: 3,

    isolateFocusedStateBoundaries: true,

    hoveredStateId: null,
    hoveredStateName: null,

};

/**
 * Executes an AlaSQL query to parse target database files safely
 */
async function runQuery(query) {
    try { 
        return await alasql.promise(query); 
    } catch (error) { 
        console.error("SQL Error:", error); 
        return []; 
    }
}

/**
 * Translates a normalized CDI value into its corresponding official standard RGB color
 */
function getOfficialCDIColor(val, minVal, maxVal) {
    const norm = (val - minVal) / (maxVal - minVal || 1);
    if (norm < 0.12) return [115, 0, 0];
    if (norm < 0.25) return [230, 0, 0];
    if (norm < 0.40) return [255, 170, 0];
    if (norm < 0.55) return [255, 255, 0];
    if (norm < 0.75) return [170, 255, 170];
    return [56, 168, 0];
}

/**
 * Handles state boundary and ID acquisition and sets matrix values
 */
async function loadStateData() {
    const queryState = `
        SELECT CAST([lat] AS FLOAT) AS lat, CAST([lng] AS FLOAT) AS lng, CAST([value] AS INT) AS val 
        FROM csv('./states_with_boundaries.csv', {headers:true, separator:','}) 
        WHERE CAST([value] AS INT) >= 1
    `;
    const dataState = await runQuery(queryState);
    
    dataState.forEach(p => {
        const r = Math.round((state.base.long_N - p.lat) / state.stateStep);
        const c = Math.round((p.lng - state.base.lat_W) / state.stateStep);
        if (r >= 0 && r < state.stateRows && c >= 0 && c < state.stateCols) {
            state.gridState[r][c] = p.val;
        }
    });
}

/**
 * Handles internal state boundary vector paths acquisition
 */
async function loadStateVectorBoundaries() {
    try {
        const response = await fetch('./state_vector_boundaries.json');
        state.stateVectorBoundaries = await response.json();
        console.log(`Loaded ${state.stateVectorBoundaries.length} vector paths.`);
    } catch (e) {
        console.error("Failed to load vector boundaries:", e);
    }
}

/**
 * Handles mainland country boundary coordinates acquisition
 */
async function loadMainlandBoundaryData() {
    const queryMainland = `
        SELECT CAST([lat] AS FLOAT) AS lat, CAST([lng] AS FLOAT) AS lng 
        FROM csv('./india_mainland_boundary.csv', {headers:true, separator:','})
    `;
    state.mainlandBoundary = await runQuery(queryMainland);
    console.log(`Loaded ${state.mainlandBoundary.length} mainland boundary track points.`);
}

/**
 * Converts screen pixel coordinates into coordinate degrees (Lat/Lng)
 */
function screenToGeo(x, y) {
    const degWidth = state.lat_E - state.lat_W;
    const degHeight = state.long_N - state.long_S;
    const lng = state.lat_W + (x / state.plotWidth) * degWidth;
    const lat = state.long_N - (y / state.plotHeight) * degHeight;
    return { lat, lng };
}

/**
 * Matches real-world spatial coordinates to data matrix row/column grid indexes
 */
function geoToGridIndices(lat, lng) {
    const r = Math.round((state.base.long_N - lat) / state.dataStep);
    const c = Math.round((lng - state.base.lat_W) / state.dataStep);
    const clampedR = Math.min(Math.max(0, r), state.totalRows - 1);
    const clampedC = Math.min(Math.max(0, c), state.totalCols - 1);
    return { r: clampedR, c: clampedC };
}

/**
 * Resets map scale parameters back to default broad configuration bounds
 */
function resetZoom() {
    state.lat_W = state.base.lat_W; 
    state.lat_E = state.base.lat_E;
    state.long_N = state.base.long_N; 
    state.long_S = state.base.long_S;

    state.selectedStateId = null;

    renderStaticMap();
    renderDynamicHUD();
}

/**
 * Scans the underlying state matrix to calculate the geometric envelope of a state 
 * and fits the canvas camera smoothly over it with a 0.5-degree margin cushion.
 */
function zoomToStateBoundingBox(stateId) {
    if (!state.gridState) return;

    state.selectedStateId = stateId;

    let minR = Infinity, maxR = -Infinity;
    let minC = Infinity, maxC = -Infinity;

    // Scan the matrix index footprint to find the extreme bounds of the selected ID
    for (let r = 0; r < state.stateRows; r++) {
        for (let c = 0; c < state.stateCols; c++) {
            if (state.gridState[r][c] === stateId) {
                if (r < minR) minR = r;
                if (r > maxR) maxR = r;
                if (c < minC) minC = c;
                if (c > maxC) maxC = c;
            }
        }
    }

    // If matching coordinate indices are found, convert back to geographic degrees
    if (minR !== Infinity) {
        // Row 0 is North (top), max row is South (bottom)
        let maxLat = state.base.long_N - (minR * state.stateStep);
        let minLat = state.base.long_N - (maxR * state.stateStep);
        let minLng = state.base.lat_W + (minC * state.stateStep);
        let maxLng = state.base.lat_W + (maxC * state.stateStep);

        // Add a 0.5-degree padding envelope so the state doesn't look squeezed against the edge
        const padding = 0.5;
        let targetMinLng = minLng - padding;
        let targetMaxLng = maxLng + padding;
        let targetMaxLat = maxLat + padding;
        let targetMinLat = minLat - padding;

        // ============================================================
        // FIXED: ASPECT RATIO CORRECTION
        // ============================================================
        // 1. Calculate raw dimensions of the target bounding box
        let geoWidth = targetMaxLng - targetMinLng;
        let geoHeight = targetMaxLat - targetMinLat;

        // 2. Determine target aspect ratio from actual canvas plot dimensions
        const canvasAspectRatio = state.plotWidth / state.plotHeight; 
        const currentAspectRatio = geoWidth / geoHeight;

        // 3. Expand the smaller axis from its center point to balance the scale
        if (currentAspectRatio > canvasAspectRatio) {
            // Box is wider than canvas ratio -> Expand height
            const desiredHeight = geoWidth / canvasAspectRatio;
            const heightDiff = desiredHeight - geoHeight;
            targetMaxLat += heightDiff / 2;
            targetMinLat -= heightDiff / 2;
        } else {
            // Box is taller than canvas ratio -> Expand width
            const desiredWidth = geoHeight * canvasAspectRatio;
            const widthDiff = desiredWidth - geoWidth;
            targetMaxLng += widthDiff / 2;
            targetMinLng -= widthDiff / 2;
        }

        // 4. Safely clamp final coordinates inside overall baseline reference maps
        state.lat_W = Math.max(state.base.lat_W, targetMinLng);
        state.lat_E = Math.min(state.base.lat_E, targetMaxLng);
        state.long_N = Math.min(state.base.long_N, targetMaxLat);
        state.long_S = Math.max(state.base.long_S, targetMinLat);
        // ============================================================
        // Re-render map structures to fit the newly adjusted frame bounds
        renderStaticMap();
    }
}

/**
 * Renders the primary CDI heat map layer on the raster canvas
 */
function renderStaticMap() {
    if (!state.gridCDI) return;

    c_raster.fillStyle = "#ffffff";
    c_raster.fillRect(0, 0, C_raster.width, C_raster.height);

    const degWidth = state.lat_E - state.lat_W;
    const degHeight = state.long_N - state.long_S;

    const baseDegWidth = state.base.lat_E - state.base.lat_W;
    const zoomRatio = baseDegWidth / degWidth; 
    const interpolationFactor = Math.min(Math.max(state.INTERP, Math.round(state.INTERP * zoomRatio)), 4*state.INTERP);

    const stepSizeDeg = state.dataStep / interpolationFactor;
    const blockWidthPx = (stepSizeDeg / degWidth) * state.plotWidth;
    const blockHeightPx = (stepSizeDeg / degHeight) * state.plotHeight;

    for (let r = 0; r < state.totalRows - 1; r++) {
        for (let col = 0; col < state.totalCols - 1; col++) {
            
            // FIX: Read each corner completely independently of the others
            const v00 = state.gridCDI[r]?.[col] ?? null;
            const v01 = state.gridCDI[r]?.[col + 1] ?? null;
            const v10 = state.gridCDI[r + 1]?.[col] ?? null;
            const v11 = state.gridCDI[r + 1]?.[col + 1] ?? null;

            // Only skip if ALL four corners are completely empty ocean points
            if (v00 === null && v01 === null && v10 === null && v11 === null) continue;

            // Extract a guaranteed valid land value to use as a fallback
            const fallbackLandValue = v00 ?? v01 ?? v10 ?? v11;
            const validV00 = v00 ?? fallbackLandValue;
            const validV01 = v01 ?? fallbackLandValue;
            const validV10 = v10 ?? fallbackLandValue;
            const validV11 = v11 ?? fallbackLandValue;

            for (let ir = 0; ir < interpolationFactor; ir++) {
                const rWeight = ir / interpolationFactor;
                const currentLat = state.base.long_N - ((r + 0.5 + rWeight) * state.dataStep);
                if (currentLat > state.long_N || currentLat < state.long_S) continue;

                for (let ic = 0; ic < interpolationFactor; ic++) {
                    const cWeight = ic / interpolationFactor;
                    const currentLng = state.base.lat_W + ((col + 0.5 + cWeight) * state.dataStep);
                    if (currentLng < state.lat_W || currentLng > state.lat_E) continue;

                    // PER-PIXEL MAP MASKING FILTER
                    if (state.gridState) {
                        const sR = Math.round((state.base.long_N - currentLat) / state.stateStep);
                        const sC = Math.round((currentLng - state.base.lat_W) / state.stateStep);
                        const cellStateId = state.gridState[sR]?.[sC] ?? null;

                        if (state.selectedStateId !== null) {
                            // If a single state is selected, only draw pixels belonging to that state
                            if (cellStateId !== state.selectedStateId) continue;
                        } else {
                            // Otherwise, only draw if inside India's landmass (boundary = 1, state ID > 1)
                            if (!cellStateId || cellStateId < 1) continue;
                        }
                    }

                    // // Bilinear Interpolation of Climate Value
                    // const topInterp = validV00 * (1 - cWeight) + validV01 * cWeight;
                    // const bottomInterp = validV10 * (1 - cWeight) + validV11 * cWeight;
                    // const interpolatedValue = topInterp * (1 - rWeight) + bottomInterp * rWeight;

                    // =========================================================
                    // CORRECTED: Adaptive Interpolation 
                    // =========================================================
                    let interpolatedValue;

                    // Check the RAW matrix directly to see if any corner is actually missing
                    const raw00 = state.gridCDI[r]?.[col] ?? null;
                    const raw01 = state.gridCDI[r]?.[col + 1] ?? null;
                    const raw10 = state.gridCDI[r + 1]?.[col] ?? null;
                    const raw11 = state.gridCDI[r + 1]?.[col + 1] ?? null;

                    if (raw00 !== null && raw01 !== null && raw10 !== null && raw11 !== null) {
                        // All 4 real data points exist -> Smooth Bilinear
                        const topInterp = validV00 * (1 - cWeight) + validV01 * cWeight;
                        const bottomInterp = validV10 * (1 - cWeight) + validV11 * cWeight;
                        interpolatedValue = topInterp * (1 - rWeight) + bottomInterp * rWeight;
                    } else {
                        // Edge pixel with missing data -> Nearest Neighbor
                        const isTop = rWeight < 0.5;
                        const isLeft = cWeight < 0.5;
                        
                        // Grab the nearest RAW value
                        if (isTop && isLeft) interpolatedValue = raw00;
                        else if (isTop && !isLeft) interpolatedValue = raw01;
                        else if (!isTop && isLeft) interpolatedValue = raw10;
                        else interpolatedValue = raw11;
                        
                        // If the mathematically nearest neighbor itself is an empty ocean point, 
                        // snap to the closest guaranteed valid landmass value.
                        if (interpolatedValue === null) {
                            interpolatedValue = validV00; 
                        }
                    }

                    const px = state.margin.left + ((currentLng - state.lat_W) / degWidth) * state.plotWidth;
                    const py = state.margin.top + ((state.long_N - currentLat) / degHeight) * state.plotHeight;

                    const [rgbR, rgbG, rgbB] = getOfficialCDIColor(interpolatedValue, state.minVal, state.maxVal);
                    c_raster.fillStyle = `rgb(${rgbR},${rgbG},${rgbB})`;
                    c_raster.fillRect(px, py, blockWidthPx + 0.3, blockHeightPx + 0.3);
                }
            }
        }
    }

    // Render State Boundaries directly onto c_raster
    if (!state.gridState) return;

    // ============================================================
    // Render Vector State Boundaries
    // ============================================================
    if (state.stateVectorBoundaries && state.stateVectorBoundaries.length > 0) {
        c_raster.save();
        c_raster.beginPath();
        c_raster.strokeStyle = "#000000"; // Black state borders
        c_raster.lineWidth = 1.5;         // Crisp, thin 1px lines
        c_raster.lineJoin = "round";

        state.stateVectorBoundaries.forEach(item => {
            if (state.isolateFocusedStateBoundaries && 
                state.selectedStateId !== null && 
                item.state_id !== state.selectedStateId) {
                return; 
            }

            let firstPoint = true;
            const pathPoints = item.coordinates || item; 
            
            pathPoints.forEach(point => {
                const degWidth = state.lat_E - state.lat_W;
                const degHeight = state.long_N - state.long_S;
                const px = state.margin.left + ((point.lng - state.lat_W) / degWidth) * state.plotWidth;
                const py = state.margin.top + ((state.long_N - point.lat) / degHeight) * state.plotHeight;
                
                if (firstPoint) { c_raster.moveTo(px, py); firstPoint = false; }
                else { c_raster.lineTo(px, py); }
            });
        });
        
        c_raster.stroke(); // Draw all paths at once (extremely fast)
        c_raster.restore();
    }

    // Render Thick Solid Mainland Country Outer Boundary Line
    if (state.mainlandBoundary && state.mainlandBoundary.length > 0 
        && ((!state.isolateFocusedStateBoundaries) || state.selectedStateId == null)
    ) {
        c_raster.save();
        c_raster.strokeStyle = "#000000"; // Set line color to solid black
        c_raster.lineWidth = 2.0;         // Define the exact thickness of your country border line
        c_raster.lineJoin = "round";
        c_raster.lineCap = "round";
        
        c_raster.beginPath();
        let firstPoint = true;
        
        for (let i = 0; i < state.mainlandBoundary.length; i++) {
            const point = state.mainlandBoundary[i];
            
            // Map the latitude/longitude point coordinates directly into current viewport pixel boundaries
            const px = state.margin.left + ((point.lng - state.lat_W) / degWidth) * state.plotWidth;
            const py = state.margin.top + ((state.long_N - point.lat) / degHeight) * state.plotHeight;
            
            if (firstPoint) {
                c_raster.moveTo(px, py);
                firstPoint = false;
            } else {
                c_raster.lineTo(px, py);
            }
        }
        
        c_raster.stroke();
        c_raster.restore();
    }
}

/**
 * Renders spatial context overlays (Tooltips & Zoom Box) on the vector layer
 */
function renderDynamicHUD() {
    c_vector.clearRect(0, 0, C_vector.width, C_vector.height);

    // Inside renderDynamicHUD(), right after c_vector.clearRect:
    if (state.hoveredStateId && state.stateVectorBoundaries) {
        c_vector.save();
        
        c_vector.strokeStyle = "#2187f4";              // Neon Magenta outline
        c_vector.fillStyle = "rgba(38, 147, 248, 0.2)";  // Translucent Magenta fill (20% opacity)
        c_vector.lineWidth = 2.5;         
        c_vector.lineJoin = "round";
        
        c_vector.beginPath();
        
        state.stateVectorBoundaries.forEach(item => {
            if (item.state_id !== state.hoveredStateId) return;
            
            let firstPoint = true;
            const pathPoints = item.coordinates || item; 
            
            pathPoints.forEach(point => {
                const degWidth = state.lat_E - state.lat_W;
                const degHeight = state.long_N - state.long_S;
                const px = state.margin.left + ((point.lng - state.lat_W) / degWidth) * state.plotWidth;
                const py = state.margin.top + ((state.long_N - point.lat) / degHeight) * state.plotHeight;
                
                if (firstPoint) { 
                    c_vector.moveTo(px, py); 
                    firstPoint = false; 
                } else { 
                    c_vector.lineTo(px, py); 
                }
            });
            
            // Close the sub-path loop so each polygon can be filled accurately
            c_vector.closePath(); 
        });
        
        // ==========================================
        // NEW: Apply both Fill and Outline
        // ==========================================
        c_vector.fill();   // Draws the semi-transparent region interior
        c_vector.stroke(); // Draws the crisp neon border
        
        c_vector.restore();
    }

    // Inside renderDynamicHUD() where tooltips are rendered:
    if (state.hoverCoords) {
        c_vector.save();
        c_vector.fillStyle = "rgba(20, 20, 20, 0.95)";
        c_vector.fillRect(state.margin.left + 15, state.margin.top + 15, 200, 80); // Height expanded to 80
        c_vector.strokeStyle = "#534d4d";
        c_vector.strokeRect(state.margin.left + 15, state.margin.top + 15, 200, 80); // Height expanded to 80
        
        c_vector.fillStyle = "#ffffff";
        c_vector.font = "bold 12px monospace";
        c_vector.fillText(`LAT : ${state.hoverCoords.lat.toFixed(4)}°N`, state.margin.left + 30, state.margin.top + 35);
        c_vector.fillText(`LNG : ${state.hoverCoords.lng.toFixed(4)}°E`, state.margin.left + 30, state.margin.top + 50);
        c_vector.fillText(`VAL : ${state.hoverCoords.val !== null ? state.hoverCoords.val.toFixed(3) : "NaN"}`, state.margin.left + 30, state.margin.top + 65);
        
        if (state.hoveredStateName) {
            c_vector.fillStyle = "#FFD700"; // Gold color for the name
            
            c_vector.fillText(
                `STATE : ${state.hoveredStateName}`, 
                state.margin.left + 30, 
                state.margin.top + 80
            ); 
        }
        
        c_vector.restore();
    }

    // Render Active Zoom Box Visualizer Canvas Boundary Window Frame
    if (state.isSelecting) {
        c_vector.save();
        c_vector.strokeStyle = "#0055ff";
        c_vector.lineWidth = 1.5;
        c_vector.setLineDash([4, 4]);
        c_vector.fillStyle = "rgba(0, 85, 255, 0.1)";
        const rectW = state.currentSelectX - state.startSelectX;
        const rectH = state.currentSelectY - state.startSelectY;
        c_vector.fillRect(state.startSelectX, state.startSelectY, rectW, rectH);
        c_vector.strokeRect(state.startSelectX, state.startSelectY, rectW, rectH);
        c_vector.restore();
    }

    // Inside renderDynamicHUD() - Render Active Date Overlay Window Frame:
    if (state.isAnimating && state.currentAnimationDateStr) {
        c_vector.save();
        c_vector.fillStyle = "rgba(20, 20, 20, 0.95)";
        c_vector.fillRect(C_vector.width - state.margin.right - 210, state.margin.top + 15, 200, 40);
        c_vector.strokeStyle = "#0055ff";
        c_vector.lineWidth = 1.5;
        c_vector.strokeRect(C_vector.width - state.margin.right - 210, state.margin.top + 15, 200, 40);
        
        c_vector.fillStyle = "#ffffff";
        c_vector.font = "bold 13px monospace";
        c_vector.textAlign = "center";
        c_vector.fillText(`WEEK: ${state.currentAnimationDateStr}`, C_vector.width - state.margin.right - 110, state.margin.top + 39);
        c_vector.restore();
    }

}

/**
 * Handles dataset CSV acquisition and sets matrix values
 */
async function loadCDIData() {
    const queryCDI = `
        SELECT CAST([0] AS FLOAT) AS lat, CAST([1] AS FLOAT) AS lng, CAST([2] AS FLOAT) AS val 
        FROM csv('./data/Current_CDI.txt', {headers:false, separator: ' '}) 
        WHERE [0] != 'NaN' AND [1] != 'NaN' AND [2] != 'NaN'
    `;
    const dataCDI = await runQuery(queryCDI);
    
    dataCDI.forEach(p => {
        const r = Math.floor((state.base.long_N - p.lat) / state.dataStep);
        const c = Math.floor((p.lng - state.base.lat_W) / state.dataStep);
        if (r >= 0 && r < state.totalRows && c >= 0 && c < state.totalCols) {
            state.gridCDI[r][c] = p.val;
        }
    });

    if (dataCDI.length > 0) {
        state.minVal = Math.min(...dataCDI.map(d => d.val));
        state.maxVal = Math.max(...dataCDI.map(d => d.val));
    }
}

/**
 * Binds mouse interactions to canvas operations
 */
function setupEventListeners() {
    C_vector.addEventListener("contextmenu", e => e.preventDefault());
    
    C_vector.addEventListener("mousedown", (e) => {
        const rect = C_vector.getBoundingClientRect();
        const x = e.clientX - rect.left - state.margin.left;
        const y = e.clientY - rect.top - state.margin.top;

        if (e.button === 0) { // Left-click triggers bounding box zoom initialization
            if (x >= 0 && x <= state.plotWidth && y >= 0 && y <= state.plotHeight) {
                state.isSelecting = true;
                state.startSelectX = e.clientX - rect.left; 
                state.startSelectY = e.clientY - rect.top;
                state.currentSelectX = state.startSelectX; 
                state.currentSelectY = state.startSelectY;
                renderDynamicHUD();
            }
        } else if (e.button === 2) { // Right click completely clears viewport zoom scaling
            resetZoom();
        }
    });

    window.addEventListener("mousemove", (e) => {
        const rect = C_vector.getBoundingClientRect();
        const x = e.clientX - rect.left - state.margin.left;
        const y = e.clientY - rect.top - state.margin.top;

        if (x >= 0 && x <= state.plotWidth && y >= 0 && y <= state.plotHeight) {
            const { lat, lng } = screenToGeo(x, y);
            const { r, c } = geoToGridIndices(lat, lng);
            const val = state.gridCDI[r]?.[c] ?? null;
            
            const sR = Math.round((state.base.long_N - lat) / state.stateStep);
            const sC = Math.round((lng - state.base.lat_W) / state.stateStep);
            const stateId = state.gridState?.[sR]?.[sC] ?? null;

            // ==========================================
            // NEW: Highlight Change Detection
            // ==========================================
            let newHoveredId = (stateId && stateId > 1) ? stateId : null;
            let newHoveredName = null;
            
            if (newHoveredId && state.stateVectorBoundaries) {
                const boundsObj = state.stateVectorBoundaries.find(b => b.state_id === newHoveredId);
                newHoveredName = boundsObj ? boundsObj.name : null;
            }

            if (state.hoveredStateId !== newHoveredId) {
                state.hoveredStateId = newHoveredId;
                state.hoveredStateName = newHoveredName;
                // renderStaticMap(); // Trigger map redraw ONLY when crossing state borders
            }
            // ==========================================

            state.hoverCoords = { lat, lng, val, stateId };
        } else { 
            state.hoverCoords = null; 
            
            // NEW: Clear highlight if mouse goes out of bounds
            if (state.hoveredStateId !== null) {
                state.hoveredStateId = null;
                state.hoveredStateName = null;
                // renderStaticMap();
            }
        }
        renderDynamicHUD();
    });

    window.addEventListener("mouseup", () => {
        if (!state.isSelecting) return;
        state.isSelecting = false;

        const xMinPx = Math.min(state.startSelectX, state.currentSelectX) - state.margin.left;
        const xMaxPx = Math.max(state.startSelectX, state.currentSelectX) - state.margin.left;
        const yMinPx = Math.min(state.startSelectY, state.currentSelectY) - state.margin.top;
        const yMaxPx = Math.max(state.startSelectY, state.currentSelectY) - state.margin.top;

        // Perform programmatic coordinate re-clipping if drawn field meets minimum resolution 
        if ((xMaxPx - xMinPx) > 10 && (yMaxPx - yMinPx) > 10) {
            const currentDegW = state.lat_E - state.lat_W;
            const currentDegH = state.long_N - state.long_S;

            const oldLatW = state.lat_W;
            const oldLongN = state.long_N;

            state.lat_W = oldLatW + (xMinPx / state.plotWidth) * currentDegW;
            state.lat_E = oldLatW + (xMaxPx / state.plotWidth) * currentDegW;
            state.long_N = oldLongN - (yMinPx / state.plotHeight) * currentDegH;
            state.long_S = oldLongN - (yMaxPx / state.plotHeight) * currentDegH;

            state.selectedStateId = null;
            
            renderStaticMap();
        } else {
            // Handles discrete clicks on the canvas
            const clickX = state.startSelectX - state.margin.left;
            const clickY = state.startSelectY - state.margin.top;
            
            if (clickX >= 0 && clickX <= state.plotWidth && clickY >= 0 && clickY <= state.plotHeight) {
                const { lat, lng } = screenToGeo(clickX, clickY);
                let sR = Math.round((state.base.long_N - lat) / state.stateStep);
                let sC = Math.round((lng - state.base.lat_W) / state.stateStep);
                let stateId = state.gridState?.[sR]?.[sC] ?? null;

                // Robustness helper: if the user clicks exactly on a border pixel (value 1),
                // scan a small 5x5 neighborhood window to grab the adjacent state ID
                if (stateId === 1) {
                    let foundId = null;
                    for (let dr = -2; dr <= 2 && !foundId; dr++) {
                        for (let dc = -2; dc <= 2; dc++) {
                            const targetId = state.gridState?.[sR + dr]?.[sC + dc];
                            if (targetId > 1) {
                                foundId = targetId;
                                break;
                            }
                        }
                    }
                    if (foundId) stateId = foundId;
                }

                // If a valid state zone was registered, calculate boundaries and zoom
                if (stateId && stateId > 1) {
                    zoomToStateBoundingBox(stateId);
                }
            }
        }
        renderDynamicHUD();
    });

    C_vector.addEventListener("mouseleave", () => { 
        state.hoverCoords = null; 
        renderDynamicHUD(); 
    });
}

/**
 * Main Orchestrator Function
 */
async function init() {
    // Canvas dimensions setup
    C_raster.width = C_vector.width = 840; 
    C_raster.height = C_vector.height = 840;
    state.plotWidth = C_vector.width - state.margin.left - state.margin.right;
    state.plotHeight = C_vector.height - state.margin.top - state.margin.bottom;

    // Calculation of grid resolution layout size parameters
    state.totalRows = Math.round((state.base.long_N - state.base.long_S) / state.dataStep) + 1;
    state.totalCols = Math.round((state.base.lat_E - state.base.lat_W) / state.dataStep) + 1;
    
    // Allocate matrix arrays
    state.gridCDI = Array(state.totalRows).fill(null).map(() => Array(state.totalCols).fill(null));

    state.stateRows = Math.round((state.base.long_N - state.base.long_S) / state.stateStep) + 1;
    state.stateCols = Math.round((state.base.lat_E - state.base.lat_W) / state.stateStep) + 1;
    state.gridState = Array(state.stateRows).fill(null).map(() => Array(state.stateCols).fill(null));

    // Execute setup components
    await loadCDIData();
    await loadStateData();
    await loadMainlandBoundaryData();
    await loadStateVectorBoundaries();
    setupEventListeners();

    // Inside init() - Bind the control panel elements:
    const btnStart = document.getElementById("btn_start_anim");
    const btnStop = document.getElementById("btn_stop_anim");
    
    if (btnStart) {
        btnStart.addEventListener("click", () => {
            const startVal = document.getElementById("anim_start_date").value;
            const endVal = document.getElementById("anim_end_date").value;
            const fpsVal = parseInt(document.getElementById("anim_fps").value, 10);
            startCDIAnimation(startVal, endVal, fpsVal);
        });
    }
    if (btnStop) {
        btnStop.addEventListener("click", stopCDIAnimation);
    }

    // Perform initial display paint operations
    renderStaticMap();
    renderDynamicHUD();
}


/**
 * Asynchronously queries and extracts a specific target week's CDI matrix values
 */
async function loadCDIDataForDate(dateStr) {
    const queryCDI = `
        SELECT CAST([0] AS FLOAT) AS lat, CAST([1] AS FLOAT) AS lng, CAST([2] AS FLOAT) AS val 
        FROM csv('./data/Drough_TS/CDI_${dateStr}.txt', {headers:false, separator: ' '}) 
        WHERE [0] != 'NaN' AND [1] != 'NaN' AND [2] != 'NaN'
    `;
    const dataCDI = await runQuery(queryCDI);
    
    if (!dataCDI || dataCDI.length === 0) {
        return false; // File missing or unreadable (signals gap jump)
    }
    
    // Completely wipe out previous frame data matrix to avoid visual ghosting/leakage
    for (let r = 0; r < state.totalRows; r++) {
        state.gridCDI[r].fill(null);
    }
    
    // Repopulate with new historical date target point coordinates
    dataCDI.forEach(p => {
        const r = Math.floor((state.base.long_N - p.lat) / state.dataStep);
        const c = Math.floor((p.lng - state.base.lat_W) / state.dataStep);
        if (r >= 0 && r < state.totalRows && c >= 0 && c < state.totalCols) {
            state.gridCDI[r][c] = p.val;
        }
    });

    return true;
}

/**
 * Orchestrates step-by-step playback loops across dates at custom framerates
 */
async function startCDIAnimation(startDateInput = "2021-07-14", endDateInput = "2024-11-13", fps = 2) {
    // Clear any active animation pipelines to prevent visual speed stacking
    stopCDIAnimation();

    let currentDate = parseDateString(startDateInput);
    const endDate = parseDateString(endDateInput);

    if (!currentDate || !endDate || currentDate > endDate) {
        console.error("Invalid animation chronological bounds provided.");
        return;
    }

    const intervalMs = 1000 / fps;
    state.isAnimating = true;

    async function animationTick() {
        if (!state.isAnimating) return;

        // Terminal animation exit evaluation point
        if (currentDate > endDate) {
            console.log("Timeline sequence completed.");
            stopCDIAnimation();
            return;
        }

        const dateStr = formatDateToYYYYMMDD(currentDate);
        state.currentAnimationDateStr = `${dateStr.substring(0, 4)}-${dateStr.substring(4, 6)}-${dateStr.substring(6, 8)}`;
        
        // Progress tracking state step ahead exactly 1 week (7 days) for Wednesday cycles
        currentDate.setDate(currentDate.getDate() + 7);

        const fileLoaded = await loadCDIDataForDate(dateStr);

        if (fileLoaded) {
            // Re-render data without modifying state viewport dimensions (preserves zoom level locks)
            renderStaticMap();
            renderDynamicHUD();
            
            // Wait for specified frame duration before evaluating the next block
            setTimeout(animationTick, intervalMs);
        } else {
            // Instant recursive hop if target data does not exist (skips large gaps smoothly)
            animationTick();
        }
    }

    // Fire initial framework loop tick
    animationTick();
}

/**
 * Safely stops and resets player controls
 */
function stopCDIAnimation() {
    state.isAnimating = false;
    state.currentAnimationDateStr = null;
    renderDynamicHUD();
}

// Internal Utility Date Parsers
function parseDateString(str) {
    const cleaned = str.replace(/-/g, "");
    if (cleaned.length !== 8) return null;
    const year = parseInt(cleaned.substring(0, 4), 10);
    const month = parseInt(cleaned.substring(4, 6), 10) - 1;
    const day = parseInt(cleaned.substring(6, 8), 10);
    return new Date(year, month, day);
}

function formatDateToYYYYMMDD(date) {
    const yyyy = date.getFullYear();
    const mm = String(date.getMonth() + 1).padStart(2, '0');
    const dd = String(date.getDate()).padStart(2, '0');
    return `${yyyy}${mm}${dd}`;
}